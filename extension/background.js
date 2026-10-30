const API = 'http://127.0.0.1:8765';
const OFFSCREEN_URL = 'offscreen.html';
const INIT = {
  stage: 'idle', tabId: null, videoId: null, jobId: null,
  message: 'Open a YouTube video to start.', error: '', progress: 0,
  originalVolume: 0.13, dubVolume: 1, subtitles: true,
  streamJobId: null, streamReady: 0, streamTotal: 0, streamChunk: 20,
  streamDuration: 0, streamBufferedTo: 0, streamProgress: 0,
  streamRanges: [], streamPlayhead: 0, streamPlayheadBufferedTo: 0
};
let creatingOffscreen = null;
let starting = false;
let finishing = false;

async function state() {
  const {dubState} = await chrome.storage.local.get('dubState');
  return {...INIT, ...(dubState || {})};
}
async function update(patch) {
  const current = await state();
  const next = {...current, ...patch};
  await chrome.storage.local.set({dubState: next});
  return next;
}
async function ensureOffscreen() {
  const url = chrome.runtime.getURL(OFFSCREEN_URL);
  const contexts = await chrome.runtime.getContexts({
    contextTypes: ['OFFSCREEN_DOCUMENT'], documentUrls: [url]
  });
  if (contexts.length) return;
  if (!creatingOffscreen) {
    creatingOffscreen = chrome.offscreen.createDocument({
      url: OFFSCREEN_URL,
      reasons: ['USER_MEDIA'],
      justification: 'Record a user-selected YouTube tab for offline pre-processing.'
    });
  }
  try { await creatingOffscreen; }
  finally { creatingOffscreen = null; }
}
async function activeYouTube() {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  if (!tab?.id || !/^https:\/\/www\.youtube\.com\/watch\?/.test(tab.url || '')) {
    throw new Error('Open a YouTube watch page first.');
  }
  return tab;
}

async function processPublicUrl() {
  const old = await state();
  if (['starting', 'recording', 'uploading', 'processing'].includes(old.stage)) {
    throw new Error('A job is already in progress.');
  }
  if (old.stage === 'playing' && old.tabId) {
    await chrome.tabs.sendMessage(old.tabId, {type: 'STOP_DUB'}).catch(() => {});
  }
  const tab = await activeYouTube();
  const check = await chrome.tabs.sendMessage(tab.id, {type: 'CHECK_VIDEO'});
  if (!check?.ok) throw new Error(check?.error || 'Video is not ready');
  if (!Number.isFinite(check.duration) || check.duration > 600 || check.duration < 1) {
    throw new Error('The demo accepts videos between 1 second and 10 minutes.');
  }
  await update({stage: 'starting', tabId: tab.id, videoId: check.videoId,
    jobId: null, error: '', progress: 0, message: 'Sending public YouTube URL to Gemini...'});
  try {
    const response = await fetch(`${API}/jobs/from-youtube`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({video_id: check.videoId, declared_duration: check.duration}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Backend HTTP ${response.status}`);
    await update({stage: 'processing', jobId: data.job_id,
      message: 'Gemini is processing the public YouTube URL...'});
    return {ok: true};
  } catch (error) {
    await update({stage: 'error', error: error.message, message: error.message});
    return {ok: false, error: error.message};
  }
}

// NEW: buffered dubbing. Clip generation happens while the user watches.
async function processProgressive() {
  const old = await state();
  if (['starting', 'recording', 'uploading', 'processing'].includes(old.stage)) {
    throw new Error('Stop the existing full-video job before starting progressive mode.');
  }
  if (old.tabId) {
    await chrome.tabs.sendMessage(old.tabId, {type: 'STOP_STREAM'}).catch(() => {});
    await chrome.tabs.sendMessage(old.tabId, {type: 'STOP_DUB'}).catch(() => {});
  }
  const tab = await activeYouTube();
  const check = await chrome.tabs.sendMessage(tab.id, {type: 'CHECK_VIDEO'});
  if (!check?.ok) throw new Error(check?.error || 'YouTube video is not ready');
  if (!(Number.isFinite(check.duration) && check.duration >= 1)) {
    throw new Error('A finite non-live video is required.');
  }
  const response = await fetch(`${API}/stream/jobs`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({video_id: check.videoId, declared_duration: check.duration})
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Backend HTTP ${response.status}`);
  await update({
    stage: 'stream_processing', tabId: tab.id, videoId: check.videoId,
    jobId: null, streamJobId: data.job_id, streamDuration: check.duration,
    streamChunk: data.chunk_seconds, streamReady: 0, streamTotal: 0,
    streamRanges: [], streamPlayhead: 0, streamPlayheadBufferedTo: 0,
    progress: 0, error: '', message: 'Gemini is preparing the first audio buffer...'
  });
  return {ok: true};
}

async function streamSnapshot(jobId) {
  const response = await fetch(`${API}/stream/jobs/${jobId}`, {cache: 'no-store'});
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `Stream HTTP ${response.status}`);
  return payload;
}

async function refreshStream(current) {
  if (!current.streamJobId || !String(current.stage).startsWith('stream_')) return current;
  try {
    const snapshot = await streamSnapshot(current.streamJobId);
    const startChunks = Math.min(2, snapshot.total_chunks);
    const readyFromStart = Array.from({length: startChunks}, (_, i) => i)
      .filter(index => snapshot.ready_chunks.includes(index)).length;
    let nextStage;
    if (snapshot.stage === 'error') nextStage = 'stream_error';
    else if (current.stage === 'stream_playing') nextStage = 'stream_playing';
    else if (readyFromStart >= startChunks) nextStage = 'stream_ready';
    else nextStage = 'stream_processing';
    return update({
      stage: nextStage, streamReady: snapshot.completed_chunks,
      streamTotal: snapshot.total_chunks, streamBufferedTo: snapshot.playable_until,
      streamDuration: snapshot.duration, streamChunk: snapshot.chunk_seconds,
      streamRanges: snapshot.ranges, streamPlayhead: snapshot.playhead,
      streamPlayheadBufferedTo: snapshot.playhead_buffered_until,
      streamProgress: snapshot.completed_chunks / snapshot.total_chunks,
      progress: snapshot.completed_chunks / snapshot.total_chunks,
      message: snapshot.stage === 'error' ? snapshot.error : snapshot.message,
      error: snapshot.error || ''
    });
  } catch (_) {
    return current; // A restart or brief offline period should not erase the job.
  }
}

async function startStreamPlayback() {
  const s = await refreshStream(await state());
  if (!s.streamJobId || !['stream_ready', 'stream_playing'].includes(s.stage)) {
    throw new Error('Buffering first two dubbed segments; try when Start playback is enabled.');
  }
  const tab = await activeYouTube();
  if (tab.id !== s.tabId) throw new Error('Switch back to the original video tab.');
  const result = await chrome.tabs.sendMessage(tab.id, {
    type: 'PLAY_STREAM', jobId: s.streamJobId, videoId: s.videoId,
    originalVolume: s.originalVolume, dubVolume: s.dubVolume,
    subtitles: s.subtitles
  });
  if (!result?.ok) throw new Error(result?.error || 'Failed to start buffered playback');
  await update({stage: 'stream_playing', message: 'Buffered dub active. Press Play on YouTube.'});
  return {ok: true};
}

async function stopStreamPlayback() {
  const s = await state();
  if (s.tabId) await chrome.tabs.sendMessage(s.tabId, {type: 'STOP_STREAM'}).catch(() => {});
  if (s.stage === 'stream_playing') await update({
    stage: 'stream_ready', message: 'Stopped dubbing. Buffered segments are still cached.'
  });
  return {ok: true};
}

async function requestStreamAsset(jobId, index, asset) {
  const s = await state();
  if (s.streamJobId !== jobId || !s.tabId) throw new Error('Unknown stream job');
  if (!Number.isInteger(index) || index < 0 || index > 100000) throw new Error('Invalid chunk index');
  const suffix = asset === 'audio' ? 'audio' : asset === 'cues' ? 'cues' : '';
  if (!suffix) throw new Error('Invalid asset type');
  const response = await fetch(`${API}/stream/jobs/${jobId}/chunks/${index}/${suffix}`);
  if (!response.ok) throw new Error(`Chunk ${index} not ready (HTTP ${response.status})`);
  if (asset === 'cues') return {ok: true, cues: await response.json()};
  // MV3 service worker is a privileged localhost fetch proxy. The content
  // script never needs broad cross-origin access to local user's media files.
  const bytes = new Uint8Array(await response.arrayBuffer());
  let encoded = '';
  for (let i = 0; i < bytes.length; i += 16384) {
    encoded += String.fromCharCode(...bytes.subarray(i, i + 16384));
  }
  return {ok: true, base64: btoa(encoded)};
}

async function setStreamPlayhead(jobId, seconds) {
  const s = await state();
  if (s.streamJobId !== jobId) return {ok: false, error: 'Unknown stream job'};
  const response = await fetch(`${API}/stream/jobs/${jobId}/seek`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({seconds: Math.max(0, Number(seconds) || 0)})
  });
  if (!response.ok) throw new Error(`Seek priority HTTP ${response.status}`);
  return {ok: true};
}

async function startCapture() {
  if (starting || finishing) throw new Error('Another recording operation is running.');
  starting = true;
  try {
    const old = await state();
    if (old.stage === 'recording' || old.stage === 'uploading') {
      throw new Error('Already recording. Finish it first.');
    }
    if (old.stage === 'playing' && old.tabId) {
      await chrome.tabs.sendMessage(old.tabId, {type: 'STOP_DUB'}).catch(() => {});
    }
    const tab = await activeYouTube();
    const check = await chrome.tabs.sendMessage(tab.id, {type: 'CHECK_VIDEO'});
    if (!check?.ok) throw new Error(check?.error || 'Video not available');
    if (!Number.isFinite(check.duration) || check.duration > 600 || check.duration < 1) {
      throw new Error('Demo supports YouTube videos between 1 second and 10 minutes.');
    }
    await update({stage: 'starting', tabId: tab.id, videoId: check.videoId,
      jobId: null, progress: 0, error: '', message: 'Connecting tab audio...'});
    await ensureOffscreen();
    const streamId = await chrome.tabCapture.getMediaStreamId({targetTabId: tab.id});
    const record = await chrome.runtime.sendMessage({
      target: 'offscreen', type: 'START_RECORD', streamId,
      videoId: check.videoId, duration: check.duration,
    });
    if (!record?.ok) throw new Error(record?.error || 'Cannot record audio');
    await update({stage: 'recording', message: 'Recording the video from the beginning...'});
    const begin = await chrome.tabs.sendMessage(tab.id, {type: 'BEGIN_CAPTURE'});
    if (!begin?.ok) {
      await chrome.runtime.sendMessage({target: 'offscreen', type: 'CANCEL_RECORD'}).catch(() => {});
      throw new Error(begin?.error || 'Cannot start the video');
    }
    return {ok: true};
  } catch (error) {
    await update({stage: 'error', error: error.message, message: error.message});
    return {ok: false, error: error.message};
  } finally {
    starting = false;
  }
}
async function finishCapture(fromEnd = false) {
  if (finishing) return {ok: false, error: 'Already finishing recording'};
  finishing = true;
  try {
    const current = await state();
    if (current.stage !== 'recording') throw new Error('No recording in progress');
    if (!fromEnd && current.tabId) {
      await chrome.tabs.sendMessage(current.tabId, {type: 'STOP_CAPTURE'}).catch(() => {});
    }
    await update({stage: 'uploading', message: 'Uploading recorded audio to local backend...'});
    const uploaded = await chrome.runtime.sendMessage({target: 'offscreen', type: 'STOP_RECORD'});
    if (!uploaded?.ok || !uploaded?.jobId) {
      throw new Error(uploaded?.error || 'Backend upload failed');
    }
    await update({stage: 'processing', jobId: uploaded.jobId, progress: 0,
      message: 'Gemini is generating the Vietnamese dubbing track...'});
    return {ok: true};
  } catch (error) {
    await update({stage: 'error', error: error.message, message: error.message});
    return {ok: false, error: error.message};
  } finally { finishing = false; }
}
async function refreshJob() {
  const current = await state();
  if (String(current.stage).startsWith('stream_')) return refreshStream(current);
  if (!current.jobId || !['processing', 'ready', 'error'].includes(current.stage)) return current;
  if (current.stage === 'ready') return current;
  try {
    const response = await fetch(`${API}/jobs/${current.jobId}`, {cache: 'no-store'});
    if (!response.ok) throw new Error(`Server HTTP ${response.status}`);
    const result = await response.json();
    if (result.stage === 'ready') {
      return update({stage: 'ready', progress: 1, error: '', message: result.message});
    }
    if (result.stage === 'error') {
      return update({stage: 'error', error: result.message, message: result.message});
    }
    return update({stage: 'processing', progress: result.progress || 0,
      message: result.message || 'Processing...'});
  } catch (_) {
    // Keep jobId: local server may temporarily be restarting.
    return current;
  }
}
async function startDub() {
  const current = await refreshJob();
  if (!current.jobId || !['ready', 'playing'].includes(current.stage)) {
    throw new Error('The audio track has not been generated yet.');
  }
  const tab = await activeYouTube();
  if (tab.id !== current.tabId) throw new Error('Switch to the original recorded YouTube tab.');
  const transcriptResponse = await fetch(`${API}/jobs/${current.jobId}/transcript`);
  if (!transcriptResponse.ok) throw new Error('Could not load the translated subtitles.');
  const cues = await transcriptResponse.json();
  const played = await chrome.tabs.sendMessage(tab.id, {
    type: 'PLAY_DUB', videoId: current.videoId,
    audioUrl: `${API}/jobs/${current.jobId}/audio`, cues,
    originalVolume: current.originalVolume, dubVolume: current.dubVolume,
    subtitles: current.subtitles,
  });
  if (!played?.ok) throw new Error(played?.error || 'Playback failed');
  await update({stage: 'playing', error: '', message: 'Dub is active. Control playback from YouTube.'});
  return {ok: true};
}
async function stopDub() {
  const current = await state();
  if (current.tabId) await chrome.tabs.sendMessage(current.tabId, {type: 'STOP_DUB'}).catch(() => {});
  if (current.stage === 'playing') await update({stage: 'ready', message: 'Dubbing paused. Ready to play again.'});
  return {ok: true};
}
async function saveSettings(settings) {
  const clamp = value => Math.max(0, Math.min(1, Number(value) || 0));
  const current = await update({
    originalVolume: clamp(settings.originalVolume),
    dubVolume: clamp(settings.dubVolume), subtitles: Boolean(settings.subtitles),
  });
  if ((current.stage === 'playing' || current.stage === 'stream_playing') && current.tabId) {
    await chrome.tabs.sendMessage(current.tabId, {
      type: 'DUB_SETTINGS', originalVolume: current.originalVolume,
      dubVolume: current.dubVolume, subtitles: current.subtitles,
    }).catch(() => {});
  }
  return {ok: true};
}
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.target === 'offscreen') return false;
  const handler = {
    GET_STATE: async () => ({ok: true, state: await refreshJob()}),
    START_PROGRESSIVE: processProgressive,
    PLAY_STREAM_REQUEST: startStreamPlayback,
    STOP_STREAM_REQUEST: stopStreamPlayback,
    STREAM_SNAPSHOT: async () => ({ok: true, snapshot: await streamSnapshot(message.jobId)}),
    STREAM_ASSET: () => requestStreamAsset(message.jobId, message.index, message.asset),
    STREAM_PLAYHEAD: () => setStreamPlayhead(message.jobId, message.seconds),
    START_URL: processPublicUrl,
    START_CAPTURE: startCapture,
    FINISH_CAPTURE: () => finishCapture(false),
    PLAY_DUB: startDub,
    STOP_DUB_REQUEST: stopDub,
    SAVE_SETTINGS: () => saveSettings(message.settings || {}),
  }[message?.type];
  if (handler) {
    Promise.resolve().then(handler).then(sendResponse)
      .catch(e => sendResponse({ok: false, error: e.message}));
    return true;
  }
  if (message?.type === 'STREAM_ENDED' && sender.tab?.id) {
    state().then(s => {
      if (s.tabId === sender.tab.id && s.stage === 'stream_playing') {
        update({stage: 'stream_ready', message: 'Video ended. Stream remains cached.'});
      }
    });
    return false;
  }
  if (message?.type === 'VIDEO_ENDED' && sender.tab?.id) {
    state().then(s => {
      if (s.tabId === sender.tab.id && s.stage === 'recording') finishCapture(true);
    });
    return false;
  }
  return false;
});
chrome.tabs.onRemoved.addListener(tabId => {
  state().then(async s => {
    if (s.tabId !== tabId) return;
    if (s.stage === 'recording') {
      await chrome.runtime.sendMessage({target: 'offscreen', type: 'CANCEL_RECORD'}).catch(() => {});
      await update({stage: 'error', message: 'Tab closed during recording.', error: 'Tab closed'});
    }
  });
});
