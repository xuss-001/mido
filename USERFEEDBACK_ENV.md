# Reproducing the initial environment

Python 3.12; install requirements-userfeedback.txt.
Run python -m pytest -o addopts= -q -p no:cacheprovider -k "not test_merge_large_midifile".
126 native functional tests are selected. Existing test_merge_large_midifile asserts a hard five-second wall-clock limit; strict-sandbox timing exceeded it during preparation. Its source remains unchanged.
MIDI fixtures can be generated in memory; no sound card, MIDI hardware, synthesizer or external service is required.
