"""CPU specialist candidate: one pass, explicit glyphs, no recovery or downloads."""
from pathlib import Path
import hashlib
import json
import math
import numpy as np

MODEL_SHA256 = 'd98ffb7e60e581483e9700a4d429069e023e5b664cb6a7fcb9dfdf61dcfdf2df'
ALPHABET_SHA256 = '0ed1103bb1a1b50ddfd5edf725e97ad3e77a66622e196562ed65fbfb66d732a0'


def prepare_band(bgr):
    """Match the exported model's training/reference preprocessing exactly."""
    import cv2
    if bgr.ndim != 3 or bgr.shape[2] != 3 or min(bgr.shape[:2]) < 1:
        raise ValueError('Invalid OCR band')
    height, width = bgr.shape[:2]
    ratio = width / height
    padded_width = int(48 * max(320/48, ratio))
    resized_width = min(padded_width, math.ceil(48 * ratio))
    resized = cv2.resize(bgr, (resized_width, 48)).astype('float32')
    resized = resized.transpose(2, 0, 1) / 255
    resized = (resized - .5) / .5
    result = np.zeros((1, 3, 48, padded_width), dtype=np.float32)
    result[0, :, :, :resized_width] = resized
    return result


def decode_ascii(prediction, characters):
    """Greedy CTC and ASCII word gaps; never change a predicted glyph."""
    values = np.asarray(prediction)
    if (values.ndim != 3 or values.shape[0] != 1 or values.shape[2] != len(characters)
            or not np.isfinite(values).all() or np.any(values < 0) or np.any(values > 1.00001)):
        raise ValueError('Invalid specialist model output')
    tokens = values[0].argmax(axis=1)
    selected = np.ones(len(tokens), dtype=bool)
    selected[1:] = tokens[1:] != tokens[:-1]
    selected &= tokens != 0
    columns = np.flatnonzero(selected)
    words = []
    current = ''
    previous = None
    scores = []
    for column in columns:
        char = characters[tokens[column]]
        scores.append(round(float(values[0, column, tokens[column]]), 5))
        if char.isspace():
            if current: words.append(current)
            current = ''
        else:
            if previous is not None and column - previous > 5 and current:
                words.append(current)
                current = ''
            current += char
        previous = column
    if current: words.append(current)
    return ' '.join(words), round(float(np.mean(scores)), 5) if scores else 0.0


class SpecialistReader:
    """V6 reader candidate. Syntax checks do not establish measured OCR accuracy."""
    def __init__(self, model_dir=None):
        import cv2  # noqa: F401 - fail once at load if preprocessing is unavailable
        import onnxruntime as ort
        folder = Path(model_dir) if model_dir else Path(__file__).parent/'models'/'specialist_v6'
        model = folder/'model.onnx'
        if hashlib.sha256(model.read_bytes()).hexdigest() != MODEL_SHA256:
            raise ValueError('Specialist model hash mismatch')
        alphabet = (folder/'characters.json').read_bytes()
        if hashlib.sha256(alphabet).hexdigest() != ALPHABET_SHA256:
            raise ValueError('Specialist alphabet hash mismatch')
        self.characters = json.loads(alphabet)
        if (len(self.characters) != 96 or self.characters[0] != 'blank'
                or self.characters[-1] != ' ' or len(set(self.characters)) != 96
                or any(len(c) != 1 or not 32 <= ord(c) < 127 for c in self.characters[1:])):
            raise ValueError('Invalid specialist alphabet')
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model), options, providers=['CPUExecutionProvider'])
        self.input_name = self.session.get_inputs()[0].name

    @staticmethod
    def capture_is_usable(frame):
        # No deterministic brightness gate ahead of the learned reader.
        valid = (frame.ndim == 3 and frame.shape[2] == 3
                 and frame.shape[1] == 675 and frame.shape[0] >= 110)
        return valid, '' if valid else 'specialist_geometry'

    def capture_region(self, monitor):
        import ctypes
        from ctypes import wintypes
        user = ctypes.windll.user32
        user.GetForegroundWindow.restype = wintypes.HWND
        user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        handle = user.GetForegroundWindow()
        title = ctypes.create_unicode_buffer(512)
        user.GetWindowTextW(handle, title, 512)
        if 'star citizen' not in title.value.lower():
            raise RuntimeError('Star Citizen is not foreground')
        rect = wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        if not user.GetClientRect(handle, ctypes.byref(rect)) or not user.ClientToScreen(handle, ctypes.byref(origin)):
            raise RuntimeError('Cannot locate game client area')
        capture_height = getattr(self, 'capture_height', 175)
        if rect.right < 675 or rect.bottom < capture_height:
            raise RuntimeError('Game window too small for specialist geometry')
        self._capture_window = handle
        return dict(left=origin.x+rect.right-675, top=origin.y, width=675, height=capture_height)

    def capture_still_valid(self):
        import ctypes
        return ctypes.windll.user32.GetForegroundWindow() == self._capture_window

    def read(self, frame, count=8):
        from sc_voice_cockpit.navpoint.learned_fields import parse_proposal
        if not self.capture_is_usable(frame)[0]:
            raise ValueError('Unsupported specialist capture geometry')
        bgr = np.asarray(frame, dtype=np.uint8)[:, :, ::-1]
        texts = []
        for index in range(min(count, 8)):
            top = 6 + 13*index
            if top+13 > bgr.shape[0]: break
            prepared = prepare_band(bgr[top:top+13])
            prediction = self.session.run(None, {self.input_name: prepared})[0]
            text, _ = decode_ascii(prediction, self.characters)
            texts.append(text)
            if text.startswith('Zone: Root Pos:'): break
        result = parse_proposal(texts)
        return 6, 0.0, result['rows']

    @staticmethod
    def capture_payload(rows):
        from sc_voice_cockpit.navpoint.learned_fields import parse_proposal
        result = parse_proposal([r['raw_model_text'] for r in rows])
        if not result['complete']:
            return None, result['reason']
        pos = result['position']
        return dict(local_frame=False, coordinate_stack=pos['coordinate_stack'], camdir=pos['camdir']), ''
