# ICS Decoder Integration - Memory Bank

## Project Overview
- **Repository**: Dysonian-Lab/icopy-x (fork of lab-401/icopy-x)
- **Feature**: ICS Decoder bridge for SEOS SIO credential cloning
- **PR**: https://github.com/lab-401/icopy-x/pull/14

## Working Configurations
- ✅ 48-bit SEOS cards → iClass Legacy/Picopass (HF 13.56 MHz)
- ✅ 26-bit SEOS cards → T5577 (LF 125 kHz)
- ✅ 26-bit SEOS cards → iClass Legacy/Picopass (HF 13.56 MHz)

## Critical Technical Details

### PM3 Return Code Behavior
- `ret=0` or `ret=1` = command completed successfully
- `ret=-1` = timeout/crash
- **IMPORTANT**: Requiring `ret == 0` will cause false failures on T5577 writes

### T5577 Detection
- Parser MUST target `[H10301]` line specifically
- Accept: "t55x7", "t5577", "t55xx" AND ("found", "chip type", "detected", "raw:")
- Use regex: `r'FC:\s*(\d+)'` and `r'CN:\s*(\d+)'` on the H10301 line only
- Do NOT use generic unanchored regexes - will match Indala/secondary decodes

### Serial Port Handling
- Baud: 115200
- Timeout: 1.2s for detection (USB CDC ACM needs >=1.0s)
- Use daemon threads for background detection
- **NEVER** call `thread.join()` in onDestroy - causes gray screen deadlock
- Close serial port with `cancel_read()`/`cancel_write()` before `close()`

### State Machine
- `STATE_DETECTING` → `STATE_READING` → `STATE_WAIT_BLANK` → `STATE_WRITING` → `RESULT`
- `STATE_DESTROYED` - set FIRST in onDestroy to stop all polling
- `_poll_decoder()` and `_poll_target()` must check `STATE_DESTROYED` before work

### Navigation
- KEY_M1 = Back button (calls `finish()` in all states except RESULT)
- KEY_PWR = Power button (always exits)
- Back button must work in ALL states

### Log Location
- `/mnt/upan/dump/ics_decoder/001.log`, `002.log`, etc.
- Each session creates new numbered file

## Files Modified
- `src/middleware/ics_decoder.py` - ICS Decoder bridge module
- `src/lib/activity_main.py` - IClassSEActivity state machine
- `src/lib/actmain.py` - MENU_ITEMS registration
- `README.md` - Documentation

## Documentation Style Notes
- Avoid heavy bullet points and emojis (✅, ⏳) - looks AI-generated
- Use paragraph-style technical writing instead of rigid tables
- "Integrates X for doing Y" = AI speak. Use "Adds X so device does Y"
- Keep technical accuracy but write like a human engineer explaining it
- PR description should be conversational, not a feature spec sheet

## Pull Request
- PR #14: https://github.com/lab-401/icopy-x/pull/14
- Submitted to lab-401/icopy-x (original repo)
- Keep PR description conversational, not a bullet-point spec
1. Gray screen on exit - caused by thread.join() in onDestroy
2. T5577 false detection - fixed by targeting H10301 line
3. T5577 write false failure - fixed by accepting ret=0 or ret=1
4. Navigation deadlock - fixed by adding STATE_DESTROYED
