# iOS Voice/Remote Probe Protocol

This protocol treats ChatGPT iOS as a black box. It does not automate the app or claim access to private telemetry.

## Preconditions

- Use a scrubbed unique canary.
- Record the explicit target terminal and baseline revision.
- Keep external notifications disabled unless separately approved.
- Do not store audio or raw transcript content.

## Matrix

Run each path with a fresh command id:

1. app foreground, screen unlocked;
2. app background, screen unlocked;
3. app background, screen locked;
4. background with Low Power Mode;
5. Wi-Fi to cellular transition;
6. audio interruption from Siri/phone/another audio app.

## Required observations

- user-reported intent capture time;
- visible audio-session state (`ui_observation` only);
- resolved target id;
- dispatch tool acknowledgement;
- unique canary observation or miss;
- terminal material-output hash;
- speech presented or approved fallback delivered.

## Interpretation

- Dynamic Island/audio alive alone: YELLOW.
- Dispatch response without canary/agent acknowledgement: YELLOW.
- Canary observed and remaining legs pending: YELLOW.
- Canary timeout or policy violation: RED.
- Every required leg proven: GREEN.

Do not infer “heard” from TTS playback. At most record `speech_presented`.
