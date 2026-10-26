# NavPoint vendored dependency patches

## OpenCV 5.0.0.93 loader: frozen NumPy 2

Applied September 15, 2026 to `cv2/__init__.py`.

Wingman AI 3.2.1 ships NumPy 2.3.0 in its PyInstaller archive. The archive
contains `numpy._core.multiarray` but omits the old forwarding module
`numpy.core.multiarray`. OpenCV's Python loader imports the old path before
loading its native extension, causing every OCR decode to fail.

If that exact legacy module (or its parent) is absent, the loader now imports
`numpy._core.multiarray`. Unrelated import errors still propagate. The native
OpenCV binary, NumPy installation, interpolation and OCR model are unchanged.
No global module aliases are inserted.

Keep this small loader patch when refreshing OpenCV unless its upstream loader
already handles frozen NumPy 2. Do not replace Wingman's NumPy installation.

Regression test: `tests/test_opencv_frozen_loader.py` imports the real bundled
OpenCV with the legacy module deliberately blocked and checks that resized OCR
pixels exactly match the normal-runtime result. An isolated PyInstaller smoke
test also passed with NumPy 2.3.0 and the legacy module excluded. That smoke
test used development Python 3.13; Wingman's real Python 3.11 live retry remains
the final integration check.
