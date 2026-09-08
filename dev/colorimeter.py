'''
Created on Feb 28, 2026

@author: bill
'''
import numpy as np
import time
import cv2
from picamera2 import Picamera2

class lights():
    def on(self):
        print ('Turning lights on')
    def off(self):
        print ('Turning lights off')

class IMX219():
    """
    Pycamera 2
    """
    self.img_H = 2464
    self.img_W = 3280

class Colorimeter(IMX219):
    """
    Using a Rpi Camera V2 as a Colorimeter 
    
    Raw image size is 3280×2464
    """
    def __init__(self, lights):
        self.lights = lights
        h, w = self.img_H, self.img_W
        p = 100  # Determine actual width and height of target area
        self.target = self.get_roi_slice((h-p)/2, (h+p)/2,
                                         (w-p)/2, (w+p)/2 )
        self.picam2 = Picamera2()
        self.picam_on = False
        self.picam_configured = False
        self.start_camera()
        
        self.rois_black = [self.get_roi_slice(a,b,c,d) for a,b,c,d in [
            (10, 20, 30, 40)
            ]]
        self.rois_white = [self.get_roi_slice(a,b,c,d) for a,b,c,d in [
            (10, 20, 30, 40)
            ]]
        self.rois_yellow = [self.get_roi_slice(a,b,c,d) for a,b,c,d in [
            (10, 20, 30, 40)
            ]]
        self.rois_brown = [self.get_roi_slice(a,b,c,d) for a,b,c,d in [
            (10, 20, 30, 40)
            ]]
        

    def get_roi_slice(self, w1, w2, h1, h2): 
        """
        Return a tuple of w and h slices.
        Usage:  ndarray[*rect_slice]
        """
        return ( slice(int(w1), int(w2)), 
                 slice(int(h1), int(h2)) )
                
    def start_camera(self):
        delay_after_start = 3.0
        if not self.picam_configured == True:
            #==========================
            # Raw Capture configuration
            #==========================
            config = self.picam2.create_still_configuration(
                raw={"size": self.picam2.sensor_resolution}
            )
            
            self.picam2.configure(config)
            
            self.picam2.set_controls({
                "AeEnable": False,
                "AwbEnable": False,
                "ExposureTime": 12000,
                "AnalogueGain": 1.0
            })
        
        if not self.picam_on == True:
            self.picam2.start()
            time.sleep(delay_after_start)

    def subtrace_black_level(raw):
        BLACK_LEVEL = 64 # or interactively get_black_level(raw) from calibration
        raw -= BLACK_LEVEL
        raw[raw < 0] = 0
        return raw

    def mean_R_B(raw):
        # R pixels: even rows, even cols
        R = raw[0::2, 0::2].mean()
    
        # B pixels: odd rows, odd cols
        B = raw[1::2, 1::2].mean()
    
        return R, B
    
    def rois_mean_R_B(self, raw, rois):
        """
        Make sure ROI alignment respects Bayer parity.
        If you shift ROI by 1 pixel, R and B extraction flips.
        To enforce correct parity:
            cy = (cy // 2) * 2
            cx = (cx // 2) * 2
        """
        R, B = 0, 0
        for roi in rois:
            r, b = self.mean_R_B(raw, roi)
            R += r
            B += b
        R /= len(rois)
        B /= len(rois)
        return R, B
            
    def capture(self, N=4):    
        """
        Capture the image N times and average
        """
        
        delay_between_image = 0.1 
        
        self.lights.on()
        
        # ===============================
        # Collect N images to be averaged
        # ===============================
        raw = self.picam2.capture_array("raw").astype("float64")

        for i in range(N):
            time.sleep(delay_between_image)
            raw += self.picam2.capture_array("raw")
        raw /= N
            
        #==========================
        print("Confirm raw format = 10 bit inside 16-bit container")
        print(raw.dtype, raw.shape)
        #==========================
        raw = raw.astype("float64")
    
        #==========================
        # Black level substraction
        #==========================
        raw = self.subtrace_black_level(raw)
        
        # =============================
        # Extrace R and B channels for test area
        # =========================================
        R_target, B_target = self.mean_R_B(raw[self.target])
        
        R_white, B_white = self.rois_mean_R_B(raw, self.rois_white)
        
        # Normalized physically meaningful metric
        # - No demosaic interpolation noise
        # - No green channel contamination
        # - No gamma distortion
        # - No ISP color matrix
        # - Pure linear photon counts
        # - Blue suppression measured directly
        R_norm = R_target / R_white 
        B_norm = B_target / B_white
        metric = R_norm / B_norm
        
        std = np.std(metric)
        
        """
        Changes:
            mean_R_B(raw) 
                return a np array of (R,B)
                Should be a single np.mean statement to get that.
            Then in rois_mean_R_B()
                RB += rois_mean_R_B(raw, rois)
            And:
                RB_norm = RBmean/RBwhite
        """
        
        
        
        
        
        
        
        
        
        