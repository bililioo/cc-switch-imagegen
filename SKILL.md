---
name: cc-switch-imagegen
description: Generate and edit raster images through a local CC Switch OpenAI-compatible relay. Use when the user asks to create, edit, localize, composite, remove backgrounds from, or produce variants of an image and the environment uses CC Switch or another Responses-compatible relay instead of the built-in image_gen tool.
---

# CC Switch Image Generation

Use this skill as the image generation workflow when requests must go through the local CC Switch relay. Preserve the imagegen prompt discipline and output expectations; replace only the transport layer.

## Transport

- Call `scripts/cc_switch_imagegen.py`; do not use the unavailable built-in `image_gen` tool.
- Default endpoint: `http://127.0.0.1:15721/v1/responses`.
- Default bearer token: `PROXY_MANAGED`; override with `CC_SWITCH_BEARER_TOKEN` without printing it.
- Default model: `gpt-5.6-sol`; override with `CC_SWITCH_IMAGE_MODEL` or `--model`. Use a relay-supported image-capable model when the user names one, such as `gpt-image-2.5-sunburst`.
- The request uses the Responses API `image_generation` tool. The relay must return an `image_generation_call` with a base64 `result`.
- Do not silently fall back to a direct OpenAI endpoint or another provider. If the relay is unavailable or rejects the request, report the concrete error.

## Use Cases

Classify the request with one consistent slug:

- `photorealistic-natural`, `product-mockup`, `ui-mockup`, `infographic-diagram`, `scientific-educational`, `ads-marketing`, `productivity-visual`
- `logo-brand`, `illustration-story`, `stylized-concept`, `historical-scene`
- Edits: `text-localization`, `identity-preserve`, `precise-object-edit`, `lighting-weather`, `background-extraction`, `style-transfer`, `compositing`, `sketch-to-render`

## Prompting

Structure prompts as:

```text
Use case: <taxonomy slug>
Asset type: <where the asset will be used>
Primary request: <the user's request>
Input images: <role of each image, if any>
Scene/backdrop: <environment>
Subject: <main subject>
Style/medium: <photo, illustration, 3D, etc.>
Composition/framing: <viewpoint and layout>
Lighting/mood: <lighting and mood>
Color palette: <palette notes>
Materials/textures: <surface details>
Text (verbatim): "<exact text>"
Constraints: <must keep/must avoid>
Avoid: <negative constraints>
```

Normalize detailed requests without inventing requirements. For generic requests, add only composition, framing, polish, or practical layout details that materially improve the result. Do not add unrelated subjects, brands, slogans, or story beats.

For edits, state invariants explicitly and repeat them on every iteration: `change only X; keep Y unchanged`. Label each input image as an edit target, style reference, or compositing source. For in-image text, quote exact copy and require verbatim rendering. For transparent assets, request `background: transparent` and preserve the returned alpha channel.

## Workflow

1. Decide whether the request is generation or editing, and classify the use case.
2. Inspect local edit targets with the image viewer before sending them. Pass them to the script with `--input-image`; do not treat style references as edit targets.
3. Build a concise structured prompt using the schema above.
4. Run the relay script. Pass `--quality`, `--size`, `--background`, and `--output-format` only when they are needed. Do not pass unsupported `input_fidelity` or mask flags.
5. Save the final image in the workspace when it is project-bound. Do not overwrite an existing asset without an explicit replacement request; choose a sibling/versioned name.
6. Validate that the response completed, the decoded file is a real image, the dimensions and format match the request, and transparent requests retain alpha.
7. Inspect the output and iterate with one targeted change at a time.
8. Report the saved path, model, relay endpoint, and the final prompt. Never print bearer tokens or full response payloads.

## Script

```bash
python3 scripts/cc_switch_imagegen.py \
  --prompt 'Use case: stylized-concept ...' \
  --model gpt-5.6-sol \
  --quality medium \
  --size 1916x821 \
  --output outputs/generated-image.png
```

Use `--input-image path/to/source.png` one or more times for edit or compositing requests. The script embeds local images as data URLs in the relay request and only accepts base64 image results in the response; it does not fetch arbitrary remote URLs.

## Failure Handling

- If the local endpoint cannot connect, tell the user CC Switch is not reachable and include the endpoint.
- If the relay returns a non-2xx response or an API error, report its status and sanitized message.
- If no completed `image_generation_call` or base64 result is present, report that the selected model/relay does not expose image generation.
- Never switch models, endpoints, or providers silently.
