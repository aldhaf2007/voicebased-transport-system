#!/usr/bin/env bash
# exit on error
set -o errexit

echo "==============================================="
echo "=== Starting Render Build Process           ==="
echo "==============================================="

echo "--> Upgrading Pip and Installing Dependencies..."
python3 -m pip install --upgrade pip
pip install -r requirements.txt

echo "--> Ensuring spaCy Language Model..."
python3 -m spacy download en_core_web_sm || true

echo "--> Checking and Downloading Kokoro TTS Models if Missing..."
python3 -c "
import os, urllib.request

models = {
    'kokoro-v1.0.onnx': 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx',
    'voices-v1.0.bin': 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin'
}

for name, url in models.items():
    if not os.path.exists(name):
        print(f'Downloading missing asset {name}...')
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as resp, open(name, 'wb') as f:
                f.write(resp.read())
            print(f'Successfully downloaded {name}.')
        except Exception as e:
            print(f'Failed to download {name}: {e}')
    else:
        print(f'Asset {name} is present.')
"

echo "--> Pre-loading Whisper Tiny Model..."
python3 -c "import whisper; whisper.load_model('tiny')"

echo "==============================================="
echo "=== Render Build Completed Successfully!    ==="
echo "==============================================="
