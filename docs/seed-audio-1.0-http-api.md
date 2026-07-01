# BytePlus Seed Audio 1\.0 HTTP API Integration Guide

# Activate the Service \(Available on June 29th\)

- Step 1: Go to BytePlus console https://console\.byteplus\.com/voice/new/setting/activate?projectName=default

- Step 2: Find "**Dola\_SeedSpeech\_Seed\_Audio\_V1**" and activate the service\.

![Image](https://internal-api-drive-stream-my.larkoffice.com/space/api/box/stream/download/authcode/?code=Njc2MDA0OTQwNzc0NTdjMTU0MjYzNWQ4MDA1Yzk1ZWJfZjI5ZGQ1MzBhN2ViYmY4NTljOGM5MGU5ZWVjNTc5OGJfSUQ6NzY1NjI4Mjk5Mjk4OTQ0MjEwNV8xNzgyODg1OTY4OjE3ODI5NzIzNjhfVjM)

- Step 3: Collect API key

![Image](https://internal-api-drive-stream-my.larkoffice.com/space/api/box/stream/download/authcode/?code=ZGQ2MjMyMzJjYzIwYmIyZGZmMTdhOWZmMTE0OWRkNGVfODFjZTRiMmU3ZThjMGUzN2RlOTgzZDNkY2IwZjlkZWNfSUQ6NzY1NjI4MzI5MjU3NzYzMTg2NV8xNzgyODg1OTY4OjE3ODI5NzIzNjhfVjM)

# Endpoint

|Item|Value|
|---|---|
|Protocol|HTTPS|
|Method|POST|
|URL|[https://voice\.ap\-southeast\-1\.bytepluses\.com/api/v3/tts/create](https://voice.ap-southeast-1.bytepluses.com/api/v3/tts/create)|
|Content\-Type|application/json|
|Output limit|Currently supports up to 120 seconds of generated audio per request\.|

# Authentication

The API supports two authentication modes\. Use only one mode per request\.

## ⭐️Recommended: New Console API Key

The new console uses single\-header authentication\.

|Header|Required|Description|
|---|---|---|
|X\-Api\-Key|Yes|API Key from the Volcengine Speech console\.|
|X\-Api\-Request\-Id|No|Client\-side trace ID\. Use an internal TraceID or UUID for troubleshooting\.|

## Legacy Console: App ID and Access Key

The legacy console uses two\-header authentication\.

|Header|Required|Description|
|---|---|---|
|X\-Api\-App\-Id|Yes|Application ID from the legacy console\.|
|X\-Api\-Access\-Key|Yes|Access key from the legacy console\.|
|X\-Api\-Request\-Id|No|Client\-side trace ID\. Use an internal TraceID or UUID for troubleshooting\.|

# Request Body

|Field|Type|Required|Description|
|---|---|---|---|
|model|string|Yes|Model identifier\. The current supported model is `seed-audio-1.0.`|
|text\_prompt|string|Yes|Prompt or text to synthesize\. Maximum length: 2048 characters\.|
|references|array|No|Reference resources\. Omit this field for text\-only generation\.|
|audio\_config|object|No|Output audio configuration\.|
|watermark|object|No|Watermark configuration object\. An empty object is accepted\.|

## Generation Modes

- **Text\-only generation:** omit references\. The API generates audio according to text\_prompt\.

- **Audio\-reference generation:** provide audio references through speaker, audio\_data, or audio\_url\. In text\_prompt, reference audio items by order using @Audio1, @Audio2, and @Audio3\.

- **Image\-reference generation:** provide one image reference through image\_data or image\_url\. text\_prompt can contain only the text to synthesize\.

# Reference Rules

|Reference Field|Description|Mutual Exclusion|
|---|---|---|
|speaker|Voice ID\. Can use a supported Doubao TTS voice or a voice\-clone voice ID\.|Choose one of speaker, audio\_data, audio\_url for an audio reference\.|
|audio\_data|Base64\-encoded reference audio\.|Choose one of speaker, audio\_data, audio\_url\.|
|audio\_url|URL of a remote reference audio file\.|Choose one of speaker, audio\_data, audio\_url\.|
|image\_data|Base64\-encoded reference image\.|Choose one of image\_data or image\_url\. Do not mix image references with audio references\.|
|image\_url|URL of a remote reference image\.|Choose one of image\_data or image\_url\. Do not mix image references with audio references\.|

## Reference Limits

- Audio references: up to 3 items per request\.

- Each reference audio file: up to 30 seconds and no more than 10 MB\.

- Supported reference audio formats: wav, mp3, pcm, ogg\_opus\.

- Image references: only 1 image per request\.

- Reference image size: no more than 10 MB\.

- Supported reference image formats: jpeg, png, webp\.

- Image references cannot be mixed with audio references in the same request\.

# Audio Configuration

|Field|Type|Default|Allowed Values or Range|
|---|---|---|---|
|format|string|wav|wav, mp3, pcm, ogg\_opus|
|sample\_rate|int|24000|8000, 16000, 24000, 32000, 44100, 48000|
|speech\_rate|int|0|\-50 to 100\. 100 means 2\.0x speed; \-50 means 0\.5x speed\.|
|loudness\_rate|int|0|\-50 to 100\. 100 means 2\.0x volume; \-50 means 0\.5x volume\.|
|pitch\_rate|int|0|\-12 to 12\.|

# Response

## Response Headers

|Header|Description|
|---|---|
|X\-Tt\-Logid|Server\-side LogID\. Provide this value when reporting or troubleshooting issues\.|

## Response Body

|Field|Type|Description|
|---|---|---|
|code|int|Status code\. Refer to the official error\-code document for details\.|
|message|string|Status details\.|
|audio|string|Generated audio data, Base64\-encoded\.|
|duration|float|Duration after speed or post\-processing, in seconds\.|
|original\_duration|float|Original model output duration in seconds\. This is used for billing and is capped at 120 seconds\.|
|url|string|Temporary audio URL\. The official document states that it is valid for 2 hours\.|

# Minimal Request Example

```bash
curl --request POST \
  --url 'https://voice.ap-southeast-1.bytepluses.com/api/v3/tts/create' \
  --max-time 120 \
  --header 'Content-Type: application/json' \
  --header 'X-Api-Key: your_api_key' \
  --data-raw '{
    "model": "seed-audio-1.0",
    "text_prompt": "Generate a short suspense radio drama in a late-night convenience store.",
    "audio_config": {
      "format": "mp3",
      "sample_rate": 48000,
      "pitch_rate": 0,
      "speech_rate": 0,
      "loudness_rate": 0
    },
    "watermark": {}
  }'
```

# Python Example

```python
import base64
import json
import requests

url = "https://voice.ap-southeast-1.bytepluses.com/api/v3/tts/create"
headers = {
    "Content-Type": "application/json",
    "X-Api-Key": "your_api_key",
}
payload = {
    "model": "seed-audio-1.0",
    "text_prompt": "Generate a short suspense radio drama in a late-night convenience store.",
    "audio_config": {
        "format": "wav",
        "sample_rate": 24000,
        "speech_rate": 0,
        "loudness_rate": 0,
        "pitch_rate": 0,
    },
    "watermark": {},
}

response = requests.post(url, headers=headers, json=payload, timeout=120)
response.raise_for_status()
data = response.json()

if "audio" in data:
    with open("output.wav", "wb") as f:
        f.write(base64.b64decode(data["audio"]))
```

# Operational Notes

- Do not hard\-code API keys in source code, documents, or shared logs\.

- Log request and response metadata for troubleshooting, but redact authentication headers\.

- Keep text\_prompt under 2048 characters\. For long scripts or audiobooks, split the content into chunks\.

- For audio references, keep the order of references aligned with @AudioN mentions in text\_prompt\.

- If the API returns a sensitive voiceprint or voice\-clone related error, simplify the voice description and avoid references to real people or distinctive real\-person voices\.

- The returned url is temporary\. Persist the Base64\-decoded audio file if you need long\-term storage\.

# Example Payloads

## Text\-only Generation

```json
{
  "model": "seed-audio-1.0",
  "text_prompt": "A rainy late-night convenience store ambience with two fictional characters speaking softly.",
  "audio_config": {
    "format": "wav",
    "sample_rate": 24000,
    "speech_rate": 0,
    "loudness_rate": 0,
    "pitch_rate": 0
  },
  "watermark": {}
}
```

## Audio Reference Generation

```json
{
  "model": "seed-audio-1.0",
  "text_prompt": "Use @Audio1 as the narrator voice and read the following line naturally: Welcome to the store.",
  "references": [
    {
      "audio_url": "https://example.com/reference.mp3"
    }
  ],
  "audio_config": {
    "format": "wav",
    "sample_rate": 24000,
    "speech_rate": 0,
    "loudness_rate": 0,
    "pitch_rate": 0
  },
  "watermark": {}
}
```

## Image Reference Generation

```json
{
  "model": "seed-audio-1.0",
  "text_prompt": "Read this scene description in a restrained suspense style.",
  "references": [
    {
      "image_url": "https://example.com/reference.png"
    }
  ],
  "audio_config": {
    "format": "wav",
    "sample_rate": 24000,
    "speech_rate": 0,
    "loudness_rate": 0,
    "pitch_rate": 0
  },
  "watermark": {}
}
```



