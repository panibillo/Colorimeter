"""
Capture 640 x 480 images interactively without shutting down

Remember that these images are cropped and do not show entire colorimeter field
of view.

Correctly handles pausing the camera for an indefinite period.
Prints out saved file name
Safely catches CTL-Z using signal

CLAUDE: This file should retain debugging messages printed to std out
"""
import time
import signal
import cv2
from picamera2 import Picamera2

def make_camera():
    print("-- make_camera()")
    picam2 = Picamera2()
    config = picam2.create_still_configuration(
        main={"size": (640, 480), "format": "RGB888"}
    )
    picam2.configure(config)
    picam2.start()
    time.sleep(2)
    return picam2

def release_camera(picam2):
    """Fully release the camera hardware resource."""
    if picam2 is not None:
        try:
            picam2.stop()
        except Exception:
            pass
        try:
            picam2.close()       # ← releases libcamera acquire() lock
        except Exception:
            pass
        del picam2
        time.sleep(0.5)          # allow hardware to fully de-register

def handle_sigtstp(signum, frame):
    raise KeyboardInterrupt("SIGTSTP received — exiting cleanly")

signal.signal(signal.SIGTSTP, handle_sigtstp)

i = 0
picam2 = None

try:
    while True:
        picam2 = make_camera()

        fname = f"/home/bill/R/savedir/rgb{i:02}.jpg"
        print(f"-- capturing to '{fname}'")
        rgb = picam2.capture_array()
        cv2.imwrite(fname, rgb)

        release_camera(picam2)   # stop + close + del
        picam2 = None            # sentinel: already fully released

        if input('Capture another? y/n ').lower() != 'y':
            break
        i += 1

except KeyboardInterrupt:
    print("\n-- Interrupted by user (Ctrl-C / Ctrl-Z)")

except Exception as e:
    print(f"\n-- Unexpected error: {e}")

finally:
    if picam2 is not None:       # interrupted mid-capture, not yet released
        print("-- Emergency camera shutdown...")
        release_camera(picam2)
    print("Camera has been correctly shut down")

print("END of script")