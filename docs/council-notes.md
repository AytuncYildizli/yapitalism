# Council Notes

The initial roadmap was reviewed by independent GPT, DS4, Grok, Claude Opus, and native Kimi K3 lanes. The usable consensus was:

- build a receipt/conformance harness, not a ChatGPT control plane;
- keep audio/channel health separate from command progress;
- require evidence provenance and never promote inference to GREEN;
- use canary/read-back semantics rather than optimistic acknowledgements;
- make Voice-specific workarounds disposable adapters;
- start local-only, with notifications and external delivery defaulting off;
- include an early kill gate before turning one opaque vendor failure into a platform.

Notable dissent:

- one lane argued the primary canary miss may be wrong-target or ended-turn behavior rather than a general Remote stall;
- the strongest naming candidates were `yapitalism` (GPT), `backchannel` (Claude), `readback` (Kimi), and `voice-receipt` (Grok);
- the initial `voice-receipt` working name was replaced by `yapitalism` before collaborator onboarding because it is more product-grade while still naming proof across unreliable boundaries.

The council is advisory. Repository behavior is governed by executable tests and direct evidence, not model consensus.
