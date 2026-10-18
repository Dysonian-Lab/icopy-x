## iCopy-X Open Source Firmware {tag}

### Two IPK variants

| File | PM3 firmware | LUA scripts | Use when |
|------|-------------|-------------|----------|
| `icopy-x-flash.ipk` | RRG/Iceman (included) | Lua 5.4 (iceman) | You want the latest PM3 client + firmware |
| `icopy-x-noflash.ipk` | Unchanged (factory) | Lua 5.1 (factory) | You want to keep your current PM3 version |

### Installation

1. Ensure that your device is up to date (1.0.90 - get it from here: https://icopyx.com/pages/update-your-icopy-x)
2. Download the IPK of your choice
3. Put your iCopy-X into PC-Mode
4. Delete ALL OTHER IPK files from the device
5. Transfer the IPK onto your device and close PC-Mode
6. Navigate to About > Update, and press [OK]
7. Device will restart
8. While restarting, the screen will flash and stay blank for up to 10 seconds. Don't panic!

If you're using the "flash" version, there will be some extra steps:

9. Device will restart and detect that you need to flash your device. Click OK.
10. Read the instructions: Make sure your device has charge, make sure it's plugged in, and then Start
11. After flashing, your device will restart.
12. While restarting, the screen will stay blank for up to 10 seconds. Don't panic!

## Companion PM3 Clients (for PC-Mode)

Connect to your iCopy-X's Proxmark module directly from your computer.

| File | PM3 version | Contents |
|------|------------|----------|
| `clients-flash.zip` | RRG/Iceman {pm3_tag} | Linux x86_64 + macOS + Windows CLI clients |
| `clients-noflash.zip` | Factory (iCopy-X original) | Linux, macOS and Windows factory clients |

### Using the companion client

1. Download the matching `clients-[flash|noflash].zip` for your IPK variant
2. Extract to a folder on your computer
3. Put your iCopy-X into PC-Mode
4. Connect via USB
5. Run: `proxmark3 /dev/ttyACM0` (Linux/macOS) or `proxmark3.exe COMx` (Windows)
Adjust `ttyACM0` and `COMx` according to the real port assigned to your iCopy-X.
