'''
Created on Feb 28, 2026

@author: bill
'''

import numpy as np
import time
import cv2
from picamera2 import Picamera2

WIDTH = 640
HEIGHT = 480

def calibrate():
    picam2 = Picamera2()
    picam2.set_controls({"AwbEnable": True})
    time.sleep(3)
    meta = picam2.capture_metadata()
    print(meta["ColourGains"])
    picam2.stop()



def bayer_capture(picam2):
    picam2 = Picamera2()

    # ---- Create deterministic configuration ----
    config = picam2.create_still_configuration(
        main={
            "size": (WIDTH, HEIGHT),
            "format": "BGR888"  # OpenCV native format
        },
        buffer_count=1
    )
    
    picam2.configure(config)
    
    # ---- Disable automatic algorithms ----
    picam2.set_controls({
        "AwbEnable": False,
        "AeEnable": False,
        "ExposureTime": 10000,        # microseconds (tune this)
        "AnalogueGain": 1.0,          # tune if needed
        "ColourGains": (1.5, 1.5),    # fixed R and B gains (tune once!)
        "NoiseReductionMode": 0,     # Off
        "Sharpness": 0.0,            # Off
        "Contrast": 1.0,             # fixed
        "Saturation": 1.0,           # fixed
    
    })
    
    picam2.start()
    
    # Allow sensor and illumination to stabilize
    time.sleep(2)
    
    
    # Capture
    frame = picam2.capture_array()
    
    # Convert to float and normalize for analysis
    M = frame.astype(np.float32) / 255.0
    
    def srgb_to_linear(x):
        """ 
        Remove gamma-compression from image. Return linear radiometric values
        
        applies nonlinear gamma ≈ 2.2.
        """
        a = 0.055
        return np.where(x <= 0.04045,
                        x / 12.92,
                        ((x + a) / (1 + a)) ** 2.4)
    
    M_linear = srgb_to_linear(M)
    
    # Save directly (already BGR)
    cv2.imwrite("measurement.png", frame)

class Colorimeter():
    """
    Using a Rpi Camera V2 as a Colorimeter 
    
    Raw image size is 3280×2464
    """
    def __init__(self):
        h, w = 2464, 3280 
        p = 100  # Determine actual width and height of target area
        self.target = slice(int((h-p)//2) : int((h+p)//2),
                            int((w-p)//2) : int((w+p)//2))
        
    def raw_capture(picam2):
    
        picam2 = Picamera2()
        
        
        #==========================
        # Raw Capture configuration
        #==========================
        config = picam2.create_still_configuration(
            raw={"size": picam2.sensor_resolution}
        )
        
        picam2.configure(config)
        
        picam2.set_controls({
            "AeEnable": False,
            "AwbEnable": False,
            "ExposureTime": 12000,
            "AnalogueGain": 1.0
        })
        
        
        #==========================
        # Raw Capture
        #==========================
        picam2.start()
        raw = picam2.capture_array("raw")
        
        picam2.stop()
            config = picam2.create_still_configuration(
                raw={"size": picam2.sensor_resolution}
                )
            raw = picam2.capture_array("raw")
            
        #==========================
        print("Confirm raw format = 10 bit inside 16-bit container")
        print(raw.dtype, raw.shape)
        #==========================
        raw = raw.astype("float64")
    
        #==========================
        # Black level substraction
        #==========================
        def subtrace_black_level(raw):
            BLACK_LEVEL = 64 # or interactively get_black_level(raw) from calibration
            raw -= BLACK_LEVEL
            raw[raw < 0] = 0
            return raw
        raw = subtrace_black_level(raw)
    
    

if __name__ == "__main__":
    picam2 = camera_setup()
    picam2.stop()




