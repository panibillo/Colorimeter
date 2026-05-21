import RPi.GPIO as GPIO
import time

BUZZER_PIN = 12  # Hardware PWM pin recommended

GPIO.setmode(GPIO.BCM)
GPIO.setup(BUZZER_PIN, GPIO.OUT)

_pwm = GPIO.PWM(BUZZER_PIN, 2000)
_pwm.start(0)


def sound_alert(frequency: int = 2000,
                volume: int = 50,
                duration: float = 1.0) -> None:
    """
    Sound the magnetic buzzer for a fixed duration.

    Parameters
    ----------
    frequency : int
        Tone frequency in Hz. Recommended range 1500-3000.
        Default 2000 Hz.
    volume : int
        Duty cycle as a percentage, 0-50. Values above 50
        do not increase loudness. Default 50 (full volume).
    duration : float
        Duration in seconds to sound the alert. Blocking.
        Default 1.0 second.
    """
    volume = max(0, min(50, volume))       # clamp to 0-50
    frequency = max(100, min(10000, frequency))  # clamp to safe range

    _pwm.ChangeFrequency(frequency)
    _pwm.ChangeDutyCycle(volume)
    time.sleep(duration)
    _pwm.ChangeDutyCycle(0)                # silence buzzer, leave PWM running

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Test the buzzer.')
    parser.add_argument('--frequency', type=int,   default=2000, help='Tone frequency in Hz (default: 2000)')
    parser.add_argument('--volume',    type=int,   default=50,   help='Duty cycle 0-50 (default: 50)')
    parser.add_argument('--duration',  type=float, default=1.0,  help='Duration in seconds (default: 1.0)')
    args = parser.parse_args()
    try:
        sound_alert(frequency=args.frequency, volume=args.volume, duration=args.duration)
    finally:
        _pwm.stop()
        GPIO.cleanup()