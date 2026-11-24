# RocketShip growth skills

Practical app-growth workflows for your AI assistant: competitor research, customer insights, creative development, video remakes, long story ads made with AI, Meta account structure and clear writing.

Browse the [RocketShip skills catalogue](https://www.rocketshiphq.com/tools/skills/) for examples, requirements and downloads. Each skill ZIP is available from [the public release](https://github.com/fortytwode/rocketship-growth-skills/releases/tag/v1.1.0), with no GitHub account required to download.

These are free skill folders. Supply your own files, assistant and any account access or provider keys a workflow requires. RocketShip accounts and client data are not included.

| Skill | What it does | What you supply |
|---|---|---|
| [Competitor Ad Analysis](skills/competitor-ad-analysis/SKILL.md) | Compares prominent ads and recent launches, groups creative mechanisms and produces a source-linked local report | Python 3.10+, your Gemini key, Chrome for Meta collection or local media |
| [Creatives From Winner](skills/creative-from-winner/SKILL.md) | Breaks down a reference ad and proposes three derivative concepts | Ad video or screenshots and transcript, product context, optional matching performance export |
| [Meta Account Structure Audit](skills/account-structure-audit/SKILL.md) | Guides a read-only structural audit using the existing public runtime | Python 3.10+, your authorized Meta account and ads_read token; primarily B2C subscription/trial apps |
| [Humanizer](skills/humanizer/SKILL.md) | Makes artificial or unnecessarily technical writing clear while preserving meaning, evidence and voice | Draft text and relevant context |
| [Video-to-video](skills/video-to-video/SKILL.md) | Guides a faithful reference remake with an explicit change list; prepares local evidence and assembles compatible prepared clips | Reference, source assets, video/audio inspection; Python and FFmpeg for helpers; your own tools for generated scenes |
| [App Store Review Analysis](skills/app-store-review-analysis/SKILL.md) | Downloads recent App Store reviews from an app ID; your assistant turns them into ranked angles and test briefs | App Store app ID and countries, verified product facts and your assistant; Python 3.10+; Google Play needs a supplied export |
| [AI Video Maker](skills/ai-video-maker/SKILL.md) | Takes an approved script to a finished story ad of about three minutes: reference images, a storyboard, a cost estimate with a spend ceiling, the scenes and the edit, with three approvals by a person | Python 3.10+, FFmpeg, and your own Replicate, Kie and ElevenLabs accounts; you pay those providers for what you generate |

## Use a skill

1. Browse the [skills/ folder](skills/) and choose a workflow. Download its matching ZIP from [the release downloads](https://github.com/fortytwode/rocketship-growth-skills/releases/tag/v1.1.0) or clone this repository.
2. Ask your assistant to read that folder's `SKILL.md` and `SETUP.md`. Follow its setup instructions before running the workflow.
3. Supply your inputs and review the result, including anything the assistant could not verify.

The folders use the `SKILL.md` format. You can copy a folder into your agent's supported skills directory, or give its instructions directly to your assistant. Installation interfaces and media capabilities vary; editor-specific installation flows have not been certified here.

The account-audit folder is a wrapper. Its implementation remains in [meta-ads-account-audit](https://github.com/fortytwode/meta-ads-account-audit). Follow the wrapper’s setup steps to check out the verified base and apply the included maintenance patch; that patch has not been merged into the public runtime’s default branch. Competitor analysis uses your Gemini account and can incur provider charges. AI Video Maker spends money on your own Replicate, Kie and ElevenLabs accounts; it estimates the cost and asks for a spend ceiling before it generates any video. The instruction-only skills need no separate API key.

Video-to-video includes local media preparation and a conservative assembly helper. It does not include a hosted AI video generator. Use your own authorized tools for new scenes, voices or substantial edits, and inspect the actual output before claiming fidelity. See its setup guide for supported audio/container limits.

App Store Review Analysis downloads recent reviews from an App Store app ID using the collection method behind Sherlock, without its cloud cache or connected systems. Python runs the downloader; your own assistant performs the analysis. Apple's recent feed is bounded to at most 500 reviews per country store; actual coverage may be smaller. Google Play needs a supplied export. Full review text, low ratings, missing data and duplicate warnings stay visible. Reviews can suggest a desire; only verified product facts can support an advertising promise. See the [fictional pilot](skills/app-store-review-analysis/examples/product-and-ads.md).

## Where things live

| Place | What belongs there |
|---|---|
| [RocketShip website](https://www.rocketshiphq.com/tools/skills/) | The front door: catalogue, requirements, examples, articles and links to skill source/downloads |
| This repository | Reusable `skills/<name>/` source, setup, references, examples, license and versioned release ZIPs (also retained in `dist/`) |
| [Mission Control](https://missioncontrol.rocketshiphq.com/skills) | Connected team editions; public packages use your own files, tools and accounts |

The existing [AI-driven creative pack](https://github.com/fortytwode/ai-driven-creatives-skill-pack), [account-audit runtime](https://github.com/fortytwode/meta-ads-account-audit) and [Mobile Growth MCP](https://www.rocketshiphq.com/mobile-growth-mcp/) retain their existing homes. This collection links them where relevant; it does not copy unrelated apps or private operations skills.

## Examples and evidence

The seven folders and their ZIPs are matched by SHA256 in [dist/manifest.json](dist/manifest.json). Local preparation/assembly, export normalization and request-failure tests verify bounded mechanics; they do not certify generative output quality, live account access, complete store coverage or campaign performance. New worked examples are labelled synthetic. A reference ad is only a verified winner when comparable performance data supports that description. Public Ad Library prominence does not reveal CPA or ROAS. Missing source data remains missing.

These workflows produce research, draft concepts, audits and proposed text. Review the output before changing campaigns, commissioning assets or publishing.

## More from RocketShip HQ

Explore the [skills catalogue](https://www.rocketshiphq.com/tools/skills/) and [Mobile Growth MCP](https://www.rocketshiphq.com/mobile-growth-mcp/), or [talk to RocketShip](https://www.rocketshiphq.com/contact-us/) about an ongoing creative and growth program.

MIT licensed; see [LICENSE](LICENSE).
