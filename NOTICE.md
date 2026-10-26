# Notices and attribution

SC Voice-Cockpit 1.2.1 by Dominicus. The combined work is GPL-3.0;
see LICENSE. The permissive components retain the full notices below.

## NavPoint modification notice (GPL-3.0 section 5)

SC_NavPoint by Mallachi, from https://github.com/Diftic/wingman-skills,
is distributed under GPL-3.0. Modified by **Dominicus, 2026**: integrated
into SC Voice-Cockpit (one combined skill and listener); hardened authenticated
local dashboard and coordinate handling; added tablet navigation and waypoint
editing; made the ring follow the game's monitor, apply settings live and
survive configuration reloads. These files are modified versions, not an
unchanged upstream distribution.

## Other credits

- SC_Keybindings 1.4.2 by Dominicus, MIT; combined adapter and emote auditing modified.
- Tablet Deck 1.6.0 by nodieCode, MIT; adapted browser client, layouts and styles.
  Source: https://github.com/nodieCode/tablet-deck (see embedded frontend history).
- OpenCV Python 5.0.0.93: the release ships `cv2/cv2.pyd` and
  `cv2/opencv_videoio_ffmpeg500_64.dll` under `navpoint/_vendor/` from the official
  Windows amd64 PyPI wheel: https://pypi.org/project/opencv-python/5.0.0.93/ .
  The builder verifies PyPI's wheel SHA-256 and the file hashes in
  `navpoint/_vendor/provenance.json`; `dist/OPENCV-SOURCE.json` records the wheel URL.
  Python wrapper notices are in `navpoint/_vendor/cv2/LICENSE.txt` (MIT);
  OpenCV is Apache-2.0, and the bundled FFmpeg component is LGPL-2.1-or-later.
  Keep `LICENSE-3RD-PARTY.txt` intact: it contains the full OpenCV, FFmpeg and
  dependency terms, copyright holders and public contact addresses.
  Upstream source and build recipes: https://github.com/opencv/opencv-python,
  https://github.com/opencv/opencv/tree/5.0.0 and
  https://github.com/opencv/opencv_3rdparty (FFmpeg build sources).
  Those components are separately licensed, not relicensed by this project.

Unofficial fan project, not affiliated with Cloud Imperium Games or ShipBit.
Star Citizen and Wingman AI belong to their respective owners. No player
commands, profiles, pairing codes, waypoint records, game extracts or logs
are distributed. Public third-party copyright names and contacts are preserved.

## SC_Keybindings MIT notice

MIT License

Copyright (c) 2026 Dominicus

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Tablet Deck MIT notice

MIT License

Copyright (c) 2026 nodieCode

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
