# The speech sample

Scriba tests the graphics cards on this sample as soon as the speech models are there (`app/jobs.py`,
`try_devices`): how fast each card and the processor transcribe it, and how far the words they write are from the
words read (`sample-it.txt`).

- `sample-it.flac`: 27.6 seconds, 16 kHz, mono, FLAC; 396 995 bytes, SHA-256
  `05731434bfa59a31d671de36883cf194f21676dd7585ed0a2589c6b70203d8bc`.
- What it is: the first sentences of chapter I of «Le avventure di Pinocchio» by Carlo Collodi (1883; the text is in
  the public domain), read by Simona Pagliari for LibriVox.
- Source: https://archive.org/details/avventure_pinocchio_librivox, file `avventurepinocchio_01_collodi_64kb.mp3`
  (SHA-256 `e9cc10749cedf427559d76775d354761f34afbb82a501722efd457c3b649a6d1`, downloaded on 11/11/2026), from
  second 40.6 for 27.6 seconds, brought to 16 kHz mono with ffmpeg, faded in over 0.05 s and out over 0.2 s.
- Licence: public domain. The archive.org page declares `creativecommons.org/licenses/publicdomain/`, and the
  recording says so at its start («Questa è una registrazione LibriVox, tutte le registrazioni di LibriVox sono di
  dominio pubblico»). It may be given away inside Scriba with no obligation; the source and the reader are named in
  the credits all the same (`app/credits.py`).
- `sample-it.txt`: the text as Collodi wrote it, which the reading follows. On 11/11/2026 the RTX A2000, the Iris
  Xe and the processor all wrote «pezzo di catasta» where the text has «pezzo da catasta»: one word in 54, the
  reader's or whisper's (nobody has listened to tell).
