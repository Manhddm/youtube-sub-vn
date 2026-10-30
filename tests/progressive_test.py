"""Run: python tests/progressive_test.py (fully mocked; no Gemini quota)."""
import io
import json
import sys
import tempfile
import time
import wave
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import progressive


def synth_wav(client, text, path):
    with wave.open(str(path), 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(24000)
        f.writeframes(b'\0\0' * 12000)  # half second, mock voice


class FakeInteractions:
    def create(self, *, model, input, response_format, **opts):
        clip = next(x for x in input if x['type'] == 'video')
        processing = clip['processing']
        assert processing['type'] == 'static'
        assert isinstance(processing['start_offset'], int)
        assert processing['end_offset'] > processing['start_offset']
        return SimpleNamespace(output_text=json.dumps({'segments': [
            {'start': 2., 'end': 5., 'original': 'Hi there', 'vi': 'Xin chao'}
        ]}))


class FakeClient:
    interactions = FakeInteractions()


def wait_for(predicate, timeout=5.):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(): return True
        time.sleep(.05)
    return False


def main():
    original_client = progressive.gemini_client
    original_synth = progressive.synth_gemini
    progressive.gemini_client = lambda: FakeClient()
    progressive.synth_gemini = synth_wav
    try:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            job = progressive.ProgressiveJob('abcdef12345', 112., path)
            assert job.count == 6
            assert wait_for(lambda: job.snapshot()['completed_chunks'] == 4), job.snapshot()
            snap = job.snapshot()
            assert snap['stage'] == 'buffered', snap
            assert snap['playable_until'] == 80., snap
            assert sorted(snap['ready_chunks']) == [0,1,2,3], snap
            assert not (path/'chunk_00004.wav').exists(), 'Worker should NOT process unneeded future'
            with wave.open(str(path/'chunk_00000.wav')) as wav:
                assert wav.getnframes() == 20*24000, wav.getnframes()
            job.seek(108)
            assert wait_for(lambda: 5 in job.snapshot()['ready_chunks']), job.snapshot()
            assert 4 not in job.snapshot()['ready_chunks'], 'Seek should jump to requested window'
            assert job.snapshot()['playhead_buffered_until'] == 112.
            job.seek(85)
            assert wait_for(lambda: job.snapshot()['stage'] == 'complete'), job.snapshot()
            job.close()
            job2 = progressive.ProgressiveJob('abcdef12345', 112., path)
            assert job2.snapshot()['stage'] == 'complete'
            assert job2.snapshot()['completed_chunks'] == 6
            job2.close()
            print('PASS: 20s WAV chunks, 80s initial buffer, on-demand seek, cache reuse')
        fake = FakeClient()
        cues = progressive.analyze_youtube_window(fake, 'abcdef12345', 112, 20, 40)
        assert cues and cues[0]['start'] >= 20
        print('PASS: Gemini YouTube clip API payload (mocked)')
    finally:
        progressive.gemini_client = original_client
        progressive.synth_gemini = original_synth


if __name__ == '__main__':
    main()
