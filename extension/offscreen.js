const SERVER = 'http://127.0.0.1:8765';
let session = null;

async function startCapture(message) {
  if (session) throw new Error('A capture is already active');
  // Stream IDs expire quickly: consume the ID immediately.
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {mandatory: {chromeMediaSource: 'tab', chromeMediaSourceId: message.streamId}},
    video: false
  });
  try {
    const audioContext = new AudioContext();
    const source = audioContext.createMediaStreamSource(stream);
    source.connect(audioContext.destination); // Tab audio must remain audible.
    await audioContext.resume();
    const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus' : 'audio/webm';
    const recorder = new MediaRecorder(stream, {mimeType: mime});
    const chunks = [];
    recorder.addEventListener('dataavailable', event => {
      if (event.data?.size) chunks.push(event.data);
    });
    session = {stream, audioContext, recorder, chunks,
      videoId: message.videoId, duration: message.duration, mime};
    recorder.start(1000);
    return {ok: true};
  } catch (error) {
    stream.getTracks().forEach(track => track.stop());
    throw error;
  }
}

async function stopCapture(upload) {
  if (!session) throw new Error('No capture is running');
  const running = session;
  session = null;
  await new Promise(resolve => {
    if (running.recorder.state === 'inactive') return resolve();
    running.recorder.addEventListener('stop', resolve, {once: true});
    running.recorder.stop();
  });
  running.stream.getTracks().forEach(track => track.stop());
  await running.audioContext.close().catch(() => {});
  if (!upload) return {ok: true};
  const blob = new Blob(running.chunks, {type: running.mime});
  if (blob.size < 2048) throw new Error('Recording was empty');
  const body = new FormData();
  body.append('audio', blob, 'capture.webm');
  body.append('video_id', running.videoId);
  body.append('declared_duration', String(running.duration));
  let reply;
  try {
    reply = await fetch(`${SERVER}/jobs`, {method: 'POST', body});
  } catch (error) {
    throw new Error(`Cannot reach local backend at ${SERVER}: ${error.message}`);
  }
  const data = await reply.json().catch(() => ({}));
  if (!reply.ok) throw new Error(data.detail || `HTTP ${reply.status}`);
  return {ok: true, jobId: data.job_id};
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.target !== 'offscreen') return false;
  const task = message.type === 'START_RECORD' ? startCapture(message)
    : message.type === 'STOP_RECORD' ? stopCapture(true)
    : message.type === 'CANCEL_RECORD' ? (session ? stopCapture(false) : Promise.resolve({ok: true}))
    : Promise.resolve({ok: false, error: 'Unknown offscreen command'});
  task.then(sendResponse).catch(error => sendResponse({ok: false, error: error.message}));
  return true;
});
