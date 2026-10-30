// Only this file reads the YouTube HTML5 video element.
let captureVideo = null;
let playback = null;
let caption = null;
let lastCue = -1;

function getVideo() {
  return document.querySelector('video.html5-main-video') || document.querySelector('video');
}
function youtubeId() {
  return new URL(location.href).searchParams.get('v');
}
function isAdShowing() {
  return Boolean(document.querySelector('.html5-video-player.ad-showing'));
}
function stopCaptureListener() {
  if (captureVideo) captureVideo.removeEventListener('ended', onCaptureEnded);
  captureVideo = null;
}
function onCaptureEnded() {
  stopCaptureListener();
  chrome.runtime.sendMessage({type: 'VIDEO_ENDED'}).catch(() => {});
}
function captionNode() {
  const parent = document.querySelector('.html5-video-player') || document.body;
  if (!caption) {
    caption = document.createElement('div');
    caption.id = 'gemini-dub-caption';
    caption.setAttribute('aria-live', 'off');
    Object.assign(caption.style, {
      position: parent === document.body ? 'fixed' : 'absolute',
      bottom: '11%', left: '8%', width: '84%', textAlign: 'center',
      zIndex: '2147483647', pointerEvents: 'none',
      fontFamily: 'Arial, sans-serif', fontSize: 'clamp(17px, 2.1vw, 29px)',
      fontWeight: '700', color: 'white', textShadow: '0 2px 5px black, 0 0 8px black',
      whiteSpace: 'normal', display: 'none'
    });
  }
  if (caption.parentNode !== parent) parent.appendChild(caption);
  caption.style.position = parent === document.body ? 'fixed' : 'absolute';
  return caption;
}
function setCaption(text) {
  const element = captionNode();
  element.textContent = text; // Plain text, never HTML injection.
  element.style.display = playback?.subtitles && text ? 'block' : 'none';
}

function stopDub() {
  if (!playback) return;
  const {video, audio, restore, listeners, timer} = playback;
  clearInterval(timer);
  for (const [type, callback] of listeners) video.removeEventListener(type, callback);
  audio.pause();
  audio.removeAttribute('src');
  audio.load();
  if (video.isConnected) {
    video.volume = restore.volume;
    video.muted = restore.muted;
  }
  playback = null;
  lastCue = -1;
  setCaption('');
}

function showCue() {
  if (!playback) return;
  const time = playback.video.currentTime;
  const index = playback.cues.findIndex(cue => time >= cue.start && time < cue.end);
  if (index !== lastCue) {
    lastCue = index;
    setCaption(index < 0 ? '' : String(playback.cues[index].vi || ''));
  }
}
function synchronize() {
  if (!playback) return;
  const {video, audio} = playback;
  if (!Number.isFinite(video.currentTime)) return;
  if (audio.readyState >= 1) {
    if (Math.abs(audio.currentTime - video.currentTime) > 0.3) {
      try { audio.currentTime = video.currentTime; } catch (_) {}
    }
    audio.playbackRate = video.playbackRate;
    if (video.paused || video.ended) {
      if (!audio.paused) audio.pause();
    } else if (audio.paused) {
      // If the browser blocks autoplay, click Play in the YouTube page.
      audio.play().catch(() => {});
    }
  }
  showCue();
}
function activateDub(message) {
  const video = getVideo();
  if (!video || youtubeId() !== message.videoId) {
    return {ok: false, error: 'This is not the recorded YouTube video.'};
  }
  stopDub();
  stopStream();
  const audio = new Audio(message.audioUrl);
  audio.preload = 'auto';
  audio.volume = message.dubVolume;
  const restore = {volume: video.volume, muted: video.muted};
  const listeners = [];
  const on = (event, fn) => {video.addEventListener(event, fn); listeners.push([event, fn]);};
  playback = {video, audio, restore, listeners, cues: message.cues,
    subtitles: message.subtitles, timer: null};
  video.muted = false;
  video.volume = message.originalVolume;
  audio.addEventListener('loadedmetadata', synchronize);
  on('play', synchronize);
  on('pause', synchronize);
  on('seeked', synchronize);
  on('ratechange', synchronize);
  on('ended', () => {audio.pause(); setCaption('');});
  playback.timer = setInterval(() => {
    if (!video.isConnected || youtubeId() !== message.videoId) {
      stopDub();
      return;
    }
    synchronize();
  }, 250);
  synchronize();
  return {ok: true};
}
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === 'PLAY_STREAM') {
    activateStream(message).then(sendResponse)
      .catch(e => sendResponse({ok: false, error: e.message}));
    return true;
  }
  if (message?.type === 'STOP_STREAM') {stopStream(); return sendResponse({ok: true});}
  if (message?.type === 'CHECK_VIDEO') {
    const video = getVideo();
    if (!video || !youtubeId()) return sendResponse({ok: false, error: 'Open a normal YouTube watch video.'});
    if (isAdShowing()) return sendResponse({ok: false, error: 'Wait for YouTube ads to finish.'});
    if (!Number.isFinite(video.duration)) {
      return sendResponse({ok: false, error: 'Live videos are not supported.'});
    }
    stopDub();
    stopStream();
    stopCaptureListener();
    video.pause();
    return sendResponse({ok: true, videoId: youtubeId(), duration: video.duration});
  }
  if (message?.type === 'BEGIN_CAPTURE') {
    const video = getVideo();
    if (!video) return sendResponse({ok: false, error: 'Video element disappeared'});
    captureVideo = video;
    video.pause();
    video.playbackRate = 1;
    video.currentTime = 0;
    video.addEventListener('ended', onCaptureEnded, {once: true});
    video.play().then(() => sendResponse({ok: true}))
      .catch(err => {stopCaptureListener(); sendResponse({ok: false, error: err.message});});
    return true;
  }
  if (message?.type === 'STOP_CAPTURE') {
    stopCaptureListener();
    getVideo()?.pause();
    return sendResponse({ok: true});
  }
  if (message?.type === 'PLAY_DUB') return sendResponse(activateDub(message));
  if (message?.type === 'STOP_DUB') {
    stopDub();
    return sendResponse({ok: true});
  }
  if (message?.type === 'DUB_SETTINGS' && streamPlayback) {
    streamPlayback.video.volume = message.originalVolume;
    streamPlayback.gain.gain.value = message.dubVolume;
    streamPlayback.subtitles = Boolean(message.subtitles);
    streamCaption(streamPlayback);
    return sendResponse({ok: true});
  }
  if (message?.type === 'DUB_SETTINGS' && playback) {
    playback.video.volume = message.originalVolume;
    playback.audio.volume = message.dubVolume;
    playback.subtitles = Boolean(message.subtitles);
    lastCue = -1;
    showCue();
    return sendResponse({ok: true});
  }
  return false;
});

// ======================= PROGRESSIVE DUBBING ============================
// The YouTube <video> element is always the master clock. Decoded PCM chunks
// are scheduled by Web Audio against that clock, avoiding individual MP3
// element gaps between pieces and supporting pause/seek/playbackRate changes.
const STREAM_API_GAP = 0.015;
let streamPlayback = null;

function streamOverlay(s, visible, detail = 'Đang nạp tiếng Việt…') {
  if (!s) return;
  if (!s.overlay) {
    const parent = document.querySelector('.html5-video-player') || document.body;
    s.overlay = document.createElement('div');
    Object.assign(s.overlay.style, {
      position: 'absolute', top: '12%', left: '25%', width: '50%',
      padding: '9px 14px', textAlign: 'center', borderRadius: '10px',
      background: 'rgba(16, 13, 34, 0.85)', color: 'white',
      fontSize: '14px', fontFamily: 'Arial, sans-serif', fontWeight: '600',
      zIndex: '2147483647', pointerEvents: 'none', display: 'none'
    });
    parent.appendChild(s.overlay);
  }
  s.overlay.textContent = detail;
  s.overlay.style.display = visible ? 'block' : 'none';
}
function stopScheduled(s) {
  for (const entry of s.sources.values()) {
    try { entry.source.stop(); } catch (_) {}
    try { entry.source.disconnect(); } catch (_) {}
  }
  s.sources.clear();
}
function stopStream() {
  const s = streamPlayback;
  if (!s) return;
  streamPlayback = null;
  clearInterval(s.timer);
  clearInterval(s.pollTimer);
  for (const [event, callback] of s.listeners) s.video.removeEventListener(event, callback);
  stopScheduled(s);
  s.overlay?.remove();
  if (s.video.isConnected) {
    s.video.volume = s.restore.volume;
    s.video.muted = s.restore.muted;
  }
  if (caption) caption.style.display = 'none';
  s.ctx.close().catch(() => {});
}
function streamCaption(s) {
  if (streamPlayback !== s || !s.subtitles) {
    if (caption) caption.style.display = 'none';
    return;
  }
  const idx = Math.min(s.total - 1, Math.max(0, Math.floor(s.video.currentTime / s.chunkSeconds)));
  const cues = s.cues.get(idx) || [];
  const found = cues.find(c => s.video.currentTime >= c.start && s.video.currentTime < c.end);
  const el = captionNode();
  const text = found?.vi || '';
  if (el.textContent !== text) el.textContent = text;
  el.style.display = text ? 'block' : 'none';
}
async function fetchStreamChunk(s, index) {
  if (s !== streamPlayback || s.buffers.has(index) || s.fetching.has(index)) return;
  if (!s.ready.has(index)) return;
  s.fetching.add(index);
  try {
    const answer = await chrome.runtime.sendMessage({
      type: 'STREAM_ASSET', jobId: s.jobId, index, asset: 'audio'
    });
    if (!answer?.ok) throw new Error(answer?.error || 'Audio chunk request failed');
    const binary = atob(answer.base64);
    const bytes = new Uint8Array(binary.length);
    for (let j = 0; j < binary.length; j++) bytes[j] = binary.charCodeAt(j);
    const decoded = await s.ctx.decodeAudioData(bytes.buffer);
    if (s !== streamPlayback) return;
    s.buffers.set(index, decoded);
    const subtitleReply = await chrome.runtime.sendMessage({
      type: 'STREAM_ASSET', jobId: s.jobId, index, asset: 'cues'
    });
    if (subtitleReply?.ok) s.cues.set(index, subtitleReply.cues || []);
  } catch (error) {
    if (s === streamPlayback) streamOverlay(s, true, `Lỗi tải đoạn âm thanh: ${error.message}`);
  } finally {
    s.fetching.delete(index);
  }
}
function scheduleStreamChunk(s, index) {
  if (s.sources.has(index) || !s.buffers.has(index)) return;
  const t = s.video.currentTime;
  const chunkStart = index * s.chunkSeconds;
  const chunkEnd = Math.min(s.duration, chunkStart + s.chunkSeconds);
  if (t >= chunkEnd - 0.045) return;
  const rate = Math.max(0.1, s.video.playbackRate);
  const wait = Math.max(0, (chunkStart - t) / rate);
  if (wait > s.chunkSeconds * 1.1) return; // Schedule only around playhead.
  const offset = Math.max(0, t - chunkStart);
  const buffer = s.buffers.get(index);
  if (offset >= buffer.duration) return;
  const when = s.ctx.currentTime + (wait > 0 ? wait : STREAM_API_GAP);
  const source = s.ctx.createBufferSource();
  source.buffer = buffer;
  source.playbackRate.value = rate;
  source.connect(s.gain);
  const entry = {source, when, offset, rate, index};
  s.sources.set(index, entry);
  source.onended = () => {
    if (s.sources.get(index) === entry) s.sources.delete(index);
  };
  try { source.start(when, offset); }
  catch (_) { s.sources.delete(index); }
}
function startBuffering(s) {
  if (streamPlayback !== s) return;
  if (!s.video.paused) {
    s.pausedByBuffer = true;
    s.video.pause();
  }
  stopScheduled(s);
  streamOverlay(s, true, 'Đang chuẩn bị giọng đọc tiếng Việt…');
}
function streamDrive(s) {
  if (s !== streamPlayback || !s.video.isConnected || youtubeId() !== s.videoId) {
    if (s === streamPlayback) stopStream();
    return;
  }
  const index = Math.max(0, Math.min(s.total - 1,
    Math.floor(s.video.currentTime / s.chunkSeconds)));
  streamCaption(s);
  // Always cache at most five PCM chunks around playhead to bound memory.
  for (const idx of [...s.buffers.keys()]) {
    if (idx < index - 2 || idx > index + 4) s.buffers.delete(idx);
  }
  if (!s.buffers.has(index)) {
    if (s.ready.has(index)) fetchStreamChunk(s, index);
    if (!s.video.paused || s.pausedByBuffer) startBuffering(s);
    return;
  }
  if (s.pausedByBuffer) {
    s.pausedByBuffer = false;
    streamOverlay(s, false);
    s.video.play().catch(() => {
      streamOverlay(s, true, 'Nhấn Play trên YouTube để tiếp tục.');
    });
    return;
  }
  if (s.video.paused || s.video.seeking || s.video.readyState < 2) {
    stopScheduled(s);
    return;
  }
  streamOverlay(s, false);
  s.ctx.resume().catch(() => {});
  // Source in the current chunk should match the video's absolute clock.
  const current = s.sources.get(index);
  if (current && s.ctx.currentTime >= current.when) {
    const predicted = index * s.chunkSeconds + current.offset +
      (s.ctx.currentTime - current.when) * current.rate;
    if (Math.abs(predicted - s.video.currentTime) > 0.28) stopScheduled(s);
  }
  scheduleStreamChunk(s, index);
  if (index + 1 < s.total) scheduleStreamChunk(s, index + 1);
}
async function streamPoll(s) {
  if (s !== streamPlayback || s.polling) return;
  s.polling = true;
  try {
    const reply = await chrome.runtime.sendMessage({
      type: 'STREAM_SNAPSHOT', jobId: s.jobId
    });
    if (reply?.ok) {
      const data = reply.snapshot;
      s.ready = new Set(data.ready_chunks);
      if (data.stage === 'error') {
        streamOverlay(s, true, `Gemini gặp lỗi: ${data.error}`);
        return;
      }
      const idx = Math.max(0, Math.min(s.total - 1,
        Math.floor(s.video.currentTime / s.chunkSeconds)));
      for (let i = idx; i <= Math.min(s.total - 1, idx + 3); i++) {
        if (s.ready.has(i)) fetchStreamChunk(s, i);
      }
    }
    // Re-prioritize next clips when the viewer scrubs or keeps watching.
    await chrome.runtime.sendMessage({
      type: 'STREAM_PLAYHEAD', jobId: s.jobId, seconds: s.video.currentTime
    }).catch(() => {});
  } catch (error) {
    streamOverlay(s, true, `Không kết nối được backend: ${error.message}`);
  } finally {
    s.polling = false;
  }
}
async function activateStream(message) {
  const video = getVideo();
  if (!video || youtubeId() !== message.videoId) {
    return {ok: false, error: 'Please switch to the original YouTube video.'};
  }
  stopDub();
  stopStream();
  const ctx = new AudioContext({sampleRate: 24000});
  const gain = ctx.createGain();
  gain.gain.value = message.dubVolume;
  gain.connect(ctx.destination);
  const info = await chrome.runtime.sendMessage({
    type: 'STREAM_SNAPSHOT', jobId: message.jobId
  });
  if (!info?.ok) {await ctx.close(); return {ok: false, error: info?.error || 'Cannot get stream status'};}
  const data = info.snapshot;
  const s = {
    jobId: message.jobId, videoId: message.videoId, video, ctx, gain,
    chunkSeconds: data.chunk_seconds, duration: data.duration,
    total: data.total_chunks, ready: new Set(data.ready_chunks),
    subtitles: message.subtitles, buffers: new Map(), cues: new Map(),
    fetching: new Set(), sources: new Map(), overlay: null,
    listeners: [], restore: {volume: video.volume, muted: video.muted},
    timer: null, pollTimer: null, polling: false, pausedByBuffer: false
  };
  streamPlayback = s;
  video.muted = false;
  video.volume = message.originalVolume;
  const on = (name, fn) => {video.addEventListener(name, fn); s.listeners.push([name, fn]);};
  on('play', () => { if (s === streamPlayback) {s.ctx.resume().catch(() => {}); streamDrive(s);} });
  on('pause', () => {if (s === streamPlayback) stopScheduled(s);});
  on('waiting', () => {if (s === streamPlayback) stopScheduled(s);});
  on('seeking', () => {if (s === streamPlayback) { stopScheduled(s); s.pausedByBuffer = false; }});
  on('seeked', () => {if (s === streamPlayback) {streamPoll(s); streamDrive(s);} });
  on('ratechange', () => {if (s === streamPlayback) stopScheduled(s);});
  on('ended', () => {
    if (s === streamPlayback) {
      stopScheduled(s);
      streamOverlay(s, false);
      chrome.runtime.sendMessage({type: 'STREAM_ENDED'}).catch(() => {});
    }
  });
  s.timer = setInterval(() => streamDrive(s), 120);
  s.pollTimer = setInterval(() => streamPoll(s), 2300);
  await streamPoll(s);
  streamDrive(s);
  return {ok: true};
}
