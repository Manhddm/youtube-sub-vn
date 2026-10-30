const $ = id => document.getElementById(id);
const API = 'http://127.0.0.1:8765';
let current = null;
let busy = false;

async function command(type, extra = {}) {
  const response = await chrome.runtime.sendMessage({type, ...extra});
  if (!response?.ok) throw new Error(response?.error || 'Extension request failed');
  return response;
}
function render(state) {
  current = state;
  $('status').textContent = state.error || state.message || state.stage;
  const isStream = String(state.stage || '').startsWith('stream_');
  const fraction = isStream ? ((state.streamBufferedTo || 0) / (state.streamDuration || 1)) : state.progress || 0;
  $('progress').style.width = `${Math.max(0, Math.min(100, Math.round(fraction * 100)))}%`;
  const timeline = $('bufferTimeline');
  timeline.replaceChildren();
  timeline.style.display = isStream ? 'block' : 'none';
  if (isStream && state.streamDuration) {
    for (const range of (state.streamRanges || [])) {
      const segment = document.createElement('span');
      segment.className = 'ready-range';
      segment.style.left = `${Math.max(0, 100 * range.start / state.streamDuration)}%`;
      segment.style.width = `${Math.min(100, 100 * (range.end - range.start) / state.streamDuration)}%`;
      timeline.appendChild(segment);
    }
    const marker = document.createElement('span');
    marker.className = 'play-marker';
    marker.style.left = `${Math.min(100, 100 * (state.streamPlayhead || 0) / state.streamDuration)}%`;
    timeline.appendChild(marker);
  }
  $('bufferInfo').textContent = isStream
    ? `Đã dịch ${state.streamReady || 0}/${state.streamTotal || '?'} đoạn • Đệm từ vị trí xem đến ${Math.round(state.streamPlayheadBufferedTo || 0)}s`
    : 'Tạo tiếng Việt theo từng đoạn — không đợi toàn bộ video';
  $('progressive').disabled = busy || ['starting','recording','uploading','processing','playing'].includes(state.stage);
  $('progressive').textContent = isStream ? '↻ Resume / Reopen Progressive' : '▶ Start Progressive Dubbing';
  $('playStream').disabled = busy || !['stream_ready','stream_playing'].includes(state.stage);
  $('stopStream').disabled = busy || state.stage !== 'stream_playing';
  $('direct').disabled = busy || ['starting', 'recording', 'uploading', 'processing', 'stream_processing', 'stream_ready', 'stream_playing'].includes(state.stage);
  $('record').disabled = busy || ['starting', 'recording', 'uploading', 'processing', 'stream_processing', 'stream_ready', 'stream_playing'].includes(state.stage);
  $('finish').disabled = busy || state.stage !== 'recording';
  $('play').disabled = busy || !['ready', 'playing'].includes(state.stage);
  $('stop').disabled = busy || state.stage !== 'playing';
  if (document.activeElement !== $('original')) $('original').value = Math.round(state.originalVolume * 100);
  if (document.activeElement !== $('dub')) $('dub').value = Math.round(state.dubVolume * 100);
  $('originalPct').textContent = `${$('original').value}%`;
  $('dubPct').textContent = `${$('dub').value}%`;
  $('subtitles').checked = state.subtitles;
  const ready = state.jobId && ['ready', 'playing'].includes(state.stage);
  for (const [id, name] of [['audioLink', 'audio'], ['srtLink', 'subtitles']]) {
    if (ready) $(id).href = `${API}/jobs/${state.jobId}/${name}`;
    else $(id).removeAttribute('href');
  }
}
async function refresh() {
  if (busy) return;
  try {
    const answer = await command('GET_STATE');
    render(answer.state);
    if (answer.state.stage === 'idle') {
      // Health check is informative; recording still requires a running backend.
      const check = await fetch(`${API}/health`).catch(() => null);
      if (!check?.ok) $('status').textContent = 'Local backend offline. Start run_windows.bat.';
    }
  } catch (e) { $('status').textContent = e.message; }
}
async function clicked(type) {
  busy = true;
  for (const id of ['progressive','playStream','stopStream','direct', 'record', 'finish', 'play', 'stop']) $(id).disabled = true;
  $('status').textContent = 'Working...';
  try { await command(type); }
  catch (e) { $('status').textContent = e.message; }
  finally { busy = false; await refresh(); }
}
$('progressive').addEventListener('click', () => clicked('START_PROGRESSIVE'));
$('playStream').addEventListener('click', () => clicked('PLAY_STREAM_REQUEST'));
$('stopStream').addEventListener('click', () => clicked('STOP_STREAM_REQUEST'));
$('direct').addEventListener('click', () => clicked('START_URL'));
$('record').addEventListener('click', () => clicked('START_CAPTURE'));
$('finish').addEventListener('click', () => clicked('FINISH_CAPTURE'));
$('play').addEventListener('click', () => clicked('PLAY_DUB'));
$('stop').addEventListener('click', () => clicked('STOP_DUB_REQUEST'));
let savingTimer = null;
function settingsChanged() {
  $('originalPct').textContent = $('original').value + '%';
  $('dubPct').textContent = $('dub').value + '%';
  if (savingTimer) clearTimeout(savingTimer);
  savingTimer = setTimeout(async () => {
    try {
      await command('SAVE_SETTINGS', {settings: {
        originalVolume: Number($('original').value) / 100,
        dubVolume: Number($('dub').value) / 100,
        subtitles: $('subtitles').checked
      }});
    } catch (e) { $('status').textContent = e.message; }
  }, 130);
}
$('original').addEventListener('input', settingsChanged);
$('dub').addEventListener('input', settingsChanged);
$('subtitles').addEventListener('change', settingsChanged);
refresh();
setInterval(refresh, 2200);
