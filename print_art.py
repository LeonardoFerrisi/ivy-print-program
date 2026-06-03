import sys
import os

# 1. Get the absolute path of the folder your script is currently sitting in
current_dir = os.path.dirname(os.path.abspath(__file__))

# 2. Append the path to the cloned 'ivy2' subfolder
ivy2_repo_path = os.path.join(current_dir, "ivy2")
sys.path.insert(0, ivy2_repo_path)

# 3. Now Python knows to look inside that folder when you call import
try:
    import ivy2
except ImportError as e:
    print(f"Error: Could not find the 'ivy2' module. Details: {e}")
    sys.exit(1)

PRINTER_MAC = "A4:62:DF:79:5E:EB"
PRINTER_COM_PORT = "COM3"  # Windows virtual Bluetooth serial port for the Canon IVY
IMAGE_PATH = "test_image.png"

def print_to_canon_ivy(mac_address, image_path):
    if not os.path.exists(image_path):
        print(f"Error: The image file '{image_path}' was not found.")
        return

    print(f"Connecting to Canon IVY Printer at {mac_address}...")
    printer = ivy2.Ivy2Printer()

    try:
        printer.connect(mac_address, com_port=PRINTER_COM_PORT)
        print("Connected successfully.")

        print(f"Sending '{image_path}' to the printer...")
        printer.print(image_path)

        print("Success! The image is currently printing.")

    except ivy2.ReceiveTimeoutError:
        print("Connection timed out during printer handshake.")
        print("Make sure the printer is on, already paired in Windows, and close to this PC.")
        print("If needed, remove and re-pair the printer in Windows Bluetooth settings, then retry.")
    except Exception as e:
        print(f"Connection or printing failed: {e}")
    finally:
        if printer.is_connected():
            printer.disconnect()

if __name__ == "__main__":
    print_to_canon_ivy(PRINTER_MAC, IMAGE_PATH)