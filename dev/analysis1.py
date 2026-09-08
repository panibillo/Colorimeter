'''
Created on Mar 6, 2026

@author: bill
'''
import numpy as np

RGGB = np.fromfile('b1.csv', sep=',').reshape(640,480,4)
print (RGGB.shape)


def extract_channels(roi):
    R = roi[0::2, 0::2]
    B = roi[1::2, 1::2]
    G1 = roi[0::2, 1::2]
    G2 = roi[1::2, 0::2]
    return R, G1, G2, B

R, G1, G2, B = extract_channels(RGGB)

for A in RGGB, R, G1, G2, B:
    print (A.shape, A.mean(), A[0,0])