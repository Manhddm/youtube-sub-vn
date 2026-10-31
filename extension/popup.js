const $ = id => document.getElementById(id);
const API = 'http://127.0.0.1:8765';
let current = null;
let busy = false;
const STAGE_LABELS = {
  idle: 'Chưa bắt đầu',
  starting: 'Đang khởi động…',
  recording: 'Đang ghi âm tab YouTube…',
  uploading: 'Đang tải bản ghi lên backend…',
  processing: 'Gemini đang dịch toàn bộ video…',
  ready: 'Bản lồng tiếng đã sẵn sàng',
  playing: 'Đang phát bản lồng tiếng',
  stream_processing: 'Đang dịch các đoạn đầu…',
  stream_ready: 'Đã đủ bộ đệm — có thể phát',
  stream_playing: 'Đang phát bản lồng tiếng (progressive)',
  stream_error: 'Lỗi progressive',
  error: 'Lỗi'
};
const SUBTITLE_STAGE_LABELS = {
  stream_processing: 'Đang dịch phụ đề các đoạn đầu…',
  stream_ready: 'Phụ đề đã sẵn sàng — có thể hiển thị',
  stream_playing: 'Đang hiển thị phụ đề tiếng Việt',
  stream_error: 'Lỗi phụ đề'
};
const MESSAGE_LABELS = {
  'Preparing first Vietnamese audio buffer...': 'Đang chuẩn bị bộ đệm tiếng Việt đầu tiên…',
  'Preparing first Vietnamese subtitle...': 'Đang chuẩn bị phụ đề tiếng Việt đầu tiên…',
  'All Vietnamese audio chunks are cached.': 'Toàn bộ đoạn tiếng Việt đã có trong bộ đệm.',
  'All Vietnamese subtitle cues are cached.': 'Toàn bộ phụ đề tiếng Việt đã có trong bộ đệm.',
  'Buffered ahead. Waiting until playback advances...': 'Đã dịch trước; chờ bạn xem tiếp.',
  'Buffered subtitles ahead. Waiting until playback advances...':
    'Đã dịch phụ đề trước; chờ bạn xem tiếp.',
  'Entire video dubbed and cached.': 'Đã lồng tiếng và lưu toàn bộ video.',
  'Entire video subtitled and cached.': 'Đã dịch và lưu toàn bộ phụ đề.',
  'Buffered dub active. Press Play on YouTube.': 'Đã bật bản lồng tiếng; nhấn Play trên YouTube.',
  'Vietnamese subtitles active. Original audio is unchanged.':
    'Phụ đề tiếng Việt đang bật; tiếng gốc giữ nguyên.',
  'Stopped Vietnamese subtitles. Translation cache is still available.':
    'Đã tắt phụ đề; bản dịch vẫn nằm trong bộ đệm.',
  'Reconnected to the cached progressive job.':
    'Đã kết nối lại job từ bộ đệm sau khi backend khởi động lại.'
};
function translateMessage(raw) {
  if (!raw) return '';
  if (MESSAGE_LABELS[raw]) return MESSAGE_LABELS[raw];
  if (raw.startsWith('Gemini translating segment')) return 'Gemini đang dịch đoạn tiếp theo…';
  if (raw.startsWith('Generating speech')) return 'Đang tạo giọng đọc tiếng Việt…';
  if (/^Chunk \d+\/\d+ ready/.test(raw)) return 'Đoạn mới đã sẵn sàng.';
  return raw;
}
function statusLine(state) {
  if (state.stage === 'error' || state.stage === 'stream_error') {
    return `Lỗi: ${state.error || state.message || 'không rõ nguyên nhân'}`;
  }
  const subtitleMode = state.streamMode === 'subtitles' &&
    String(state.stage || '').startsWith('stream_');
  const labels = subtitleMode ? SUBTITLE_STAGE_LABELS : STAGE_LABELS;
  const label = labels[state.stage] || STAGE_LABELS[state.stage] || state.stage || '';
  const detail = translateMessage(state.message);
  if (!detail || detail === label || state.stage === 'idle') return label;
  return `${label} — ${detail}`;
}

async function command(type, extra = {}) {
  const response = await chrome.runtime.sendMessage({type, ...extra});
  if (!response?.ok) throw new Error(response?.error || 'Extension request failed');
  return response;
}
function render(state) {
  current = state;
  $('status').textContent = statusLine(state);
  const isStream = String(state.stage || '').startsWith('stream_');
  const subtitlesMode = isStream && state.streamMode === 'subtitles';
  const fraction = isStream
    ? (subtitlesMode
      ? (state.streamReady || 0) / (state.streamTotal || 1)
      : (state.streamBufferedTo || 0) / (state.streamDuration || 1))
    : state.progress || 0;
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
  $('streamHint').textContent = subtitlesMode
    ? 'Dịch phụ đề từng đoạn 20 giây và hiển thị đè lên video. Tiếng gốc và âm lượng YouTube không bị thay đổi.'
    : 'Dịch từng đoạn 20 giây. Bắt đầu nghe sau khi có bộ đệm ban đầu; Gemini chỉ tiếp tục dịch trước vị trí đang xem.';
  $('bufferInfo').textContent = isStream
    ? (subtitlesMode
      ? `Đã dịch ${state.streamReady || 0}/${state.streamTotal || '?'} đoạn phụ đề • Giữ nguyên tiếng gốc`
      : `Đã dịch ${state.streamReady || 0}/${state.streamTotal || '?'} đoạn • Đệm từ vị trí xem đến ${Math.round(state.streamPlayheadBufferedTo || 0)}s`)
    : 'Tạo tiếng Việt theo từng đoạn — không đợi toàn bộ video';
  const analysisName = isStream && state.streamAnalysisModel
    ? state.streamAnalysisModel : 'Gemini Flash';
  $('analysisModel').textContent = isStream && state.streamAnalysisFallback
    ? `${analysisName} (dự phòng)` : analysisName;
  if (subtitlesMode) {
    $('ttsRow').style.display = 'none';
  } else {
    $('ttsRow').style.display = 'flex';
    const edgeVoice = state.streamTtsProvider === 'edge';
    const ttsName = isStream
      ? (edgeVoice
        ? `Edge TTS${state.streamTtsVoice ? ` · ${state.streamTtsVoice}` : ''}`
        : (state.streamTtsModel || 'Gemini Flash TTS'))
      : 'Gemini Flash TTS';
    $('ttsModel').textContent = isStream && !edgeVoice && state.streamTtsFallback
      ? `${ttsName} (dự phòng)` : ttsName;
  }
  const classicBusy = ['starting','recording','uploading','processing','playing'].includes(state.stage);
  $('progressive').disabled = busy || classicBusy;
  $('progressive').textContent = isStream && !subtitlesMode
    ? '↻ Resume / Reopen Progressive' : '▶ Start Progressive Dubbing';
  $('subtitlesOnly').disabled = busy || classicBusy;
  $('subtitlesOnly').textContent = isStream && subtitlesMode
    ? '↻ Resume / Reopen Subtitles' : '💬 Start Vietnamese Subtitles';
  const canPlayStream = isStream && (subtitlesMode
    ? ['stream_processing','stream_ready','stream_playing'].includes(state.stage)
    : ['stream_ready','stream_playing'].includes(state.stage));
  $('playStream').disabled = busy || !canPlayStream;
  $('playStream').textContent = subtitlesMode
    ? '▶ Show Vietnamese subtitles' : '▶ Play buffered Vietnamese audio';
  $('stopStream').disabled = busy || state.stage !== 'stream_playing';
  $('stopStream').textContent = subtitlesMode ? '■ Stop subtitles' : '■ Stop streamed dubbing';
  $('direct').disabled = busy || ['starting', 'recording', 'uploading', 'processing', 'stream_processing', 'stream_ready', 'stream_playing'].includes(state.stage);
  $('record').disabled = busy || ['starting', 'recording', 'uploading', 'processing', 'stream_processing', 'stream_ready', 'stream_playing'].includes(state.stage);
  $('finish').disabled = busy || state.stage !== 'recording';
  $('play').disabled = busy || !['ready', 'playing'].includes(state.stage);
  $('stop').disabled = busy || state.stage !== 'playing';
  $('original').disabled = subtitlesMode;
  $('dub').disabled = subtitlesMode;
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
async function clicked(type, extra = {}) {
  busy = true;
  for (const id of ['progressive','subtitlesOnly','playStream','stopStream','direct', 'record', 'finish', 'play', 'stop']) $(id).disabled = true;
  $('status').textContent = 'Working...';
  try { await command(type, extra); }
  catch (e) { $('status').textContent = e.message; }
  finally { busy = false; await refresh(); }
}
$('progressive').addEventListener('click', () => clicked('START_PROGRESSIVE', {mode: 'dub'}));
$('subtitlesOnly').addEventListener('click', () => clicked('START_PROGRESSIVE', {mode: 'subtitles'}));
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
