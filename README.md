# VT Keyword Watcher

A simple Python tool that checks Virginia Tech course registration and notifies you when a seat becomes available.

---

## Files in this project

| File name | Description |
|------------|-------------|
| **vt_keyword_watcher.py** | Main program, run this to start the watcher |
| **config_keyword.json** | Set which course CRNs to watch and how often to check |
| **email_config.json** | Your email info used to send seat alerts |
| **requirements.txt** | List of Python packages to install before running |

---

## Setup Guide

### 1. Install required packages
Open a terminal in this folder and run:
pip install -r requirements.txt

### 2. Edit config_keyword.json and Edit email_config.json
Edit config_keyword.json and Edit email_config.json

### 3. Run the program
python vt_keyword_watcher.py

A Chrome browser window will open automatically.
Inside the browser:
Log in to the Virginia Tech registration system
Click Browse Classes
Select your term and click Continue
Go to the Keyword Search page
Return to the terminal and press Enter
The script will now start checking your CRNs for open seats.

### 4. When a seat opens
You’ll see a Windows toast notification on your screen
You’ll also receive an email alert if email setup is correct
Example email subject:
[VT Seat Alert] CRN 93456 seat open




## Example Folder Structure
Example Folder Structure
vt-seat-watcher/
│
├── vt_keyword_watcher.py
├── config_keyword.json
├── email_config.json
├── requirements.txt
└── README.md


License
This project is open-source and free to use for personal purposes.
Please do not share your private credentials publicly.