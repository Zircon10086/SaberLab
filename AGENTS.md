# AGENTS.md



## Purpose



You are working on **SaberLab**, a local-first Beat Saber training laboratory.



SaberLab is not merely a score viewer or replay player. Its long-term purpose is to provide a reliable, deterministic, and extensible environment for:



* Beat Saber replay analysis
* Motion telemetry
* Player performance analysis
* Training experiments
* 3D replay visualization
* Score and difficulty analysis
* AI-assisted coaching



The project may use AI/Vibe Coding extensively, but **AI-generated code must preserve the existing architecture and data integrity**.



Your primary objective is:



> \\\\\\\\\\\\\\\*\\\\\\\\\\\\\\\*Extend SaberLab without breaking SaberLab.\\\\\\\\\\\\\\\*\\\\\\\\\\\\\\\*



When implementing a feature, aim for a precise, elegant solution that fulfills the functionality first, while keeping the change surface as small as practical and balancing efficiency and safety. Never sacrifice correctness, efficiency, or result quality merely to minimize the diff.



\---



# 1\. Core Principles



These principles are mandatory.



## 1.1 Local-first



SaberLab is designed to work primarily with local data.



Prefer:



* local Beat Saber maps
* local `.bsor` replays
* local database/cache
* local deterministic analysis
* local rendering



Network services such as ScoreSaber, BeatLeader, or LLM providers are supplementary.



A network failure must not corrupt local data or prevent core local functionality from working whenever practical.



Do not make a previously local feature network-dependent without an explicit architectural decision.



\---



## 1.2 Deterministic-first



All factual replay and gameplay analysis must be deterministic.



The following belong to deterministic code:



* BSOR parsing
* score calculation
* timing calculations
* swing metrics
* motion telemetry
* path metrics
* fatigue indicators
* map analysis
* statistical calculations
* player history calculations



LLMs must not become the source of truth for numerical gameplay data.



The correct data flow is:



```text

Raw Replay / Map

      ↓

Deterministic Parsing

      ↓

Deterministic Analysis

      ↓

Structured Results

      ↓

AI Interpretation

```



Never reverse this dependency.



The AI may interpret, explain, compare, summarize, or recommend based on structured facts, but it must not invent or silently replace the underlying measurements.



\---



## 1.3 Raw Replay Integrity



Original replay files are read-only source data.



Rules:



* NEVER modify the original `.bsor` file.
* NEVER write derived analysis results into the original replay.
* NEVER use the replay file itself as a cache.
* Store derived information separately.
* Preserve reproducibility: the same replay and analysis version should produce the same deterministic result unless an intentional algorithm change has been introduced.



\---



## 1.4 Preserve Architectural Boundaries



Do not bypass existing architecture merely because a shortcut is easier.



The project is intentionally divided into logical layers.



Typical ownership:



```text

backend/

├── bsor/       Replay parsing and replay-related primitives

├── maps/       Map parsing and map-related primitives

├── analysis/   Deterministic gameplay and motion analysis

├── db/         Persistence and database access

├── services/   External integrations and service logic

├── scoresaber.py  ScoreSaber integration

├── ai/         LLM providers and AI interpretation

├── config/     Configuration schema and configuration handling

├── host.py        Local HTTP host / application integration

└── desktop.py   Desktop/WebView integration



frontend/

└── Main SaberLab UI


plugins/

└── First-party plugins (detected & loaded at startup by convention;
    no third-party plugin interface)


(仓库外) Local-ChroViewer/

└── First plugin instance: independent 3D replay application (external
    GPL-2.0 project, NOT part of this repository; build output loaded
    from plugins/chro/)

```



The exact directory structure is authoritative from the current repository. Do not invent a competing architecture.



\---



# 2\. Layer Ownership



When implementing a feature, determine which layer owns the behavior BEFORE writing code.



## Replay / BSOR



Responsible for:



* reading replay files
* decoding replay structures
* replay-level primitives



Do not place UI logic here.



\---



## Maps



Responsible for:



* map discovery
* map parsing
* map metadata
* difficulty information directly derived from maps



Do not place UI-specific logic here.



\---



## Analysis



Responsible for deterministic computation.



Examples:



* accuracy
* timing deviation
* swing velocity
* angular velocity
* cut distance
* path efficiency
* direction changes
* section statistics
* fatigue-related metrics
* other measurable replay telemetry



Rules:



* No LLM dependency.
* No UI dependency.
* No browser-specific behavior.
* No network dependency unless explicitly required by the algorithm.
* Prefer pure/reproducible functions.
* Keep numerical logic testable.



\---



## Database



Database access must remain centralized.



Current repository conventions must be respected, including the repository/model separation.



Rules:



* Do not open ad-hoc database connections in arbitrary modules.
* Do not scatter raw SQL across services or UI code.
* SQL/data access should remain inside the established database abstraction.
* Schema changes require an intentional migration/compatibility strategy.
* Do not silently delete or rename persisted fields merely to simplify implementation.



\---



## Services / External APIs



External network services belong in the service/integration layer.



Examples:



* ScoreSaber
* BeatLeader
* remote metadata
* external APIs
* future data providers



Rules:



* Handle timeouts and failures explicitly.
* Respect rate limits.
* Cache external data when appropriate.
* Do not make external services mandatory for core local analysis.
* Do not expose network credentials to frontend code.



\---



## AI



AI code belongs in the AI layer.



The AI layer may:



* consume structured analysis
* generate explanations
* compare historical results
* produce training suggestions
* call explicitly authorized tools
* maintain/consume player-level coaching context



The AI layer must NOT:



* replace deterministic analysis
* silently modify replay data
* directly manipulate the database outside approved interfaces
* bypass service abstractions
* invent numerical results
* silently modify application configuration
* perform destructive operations without explicit authorization



For future AI Agent functionality, tools must have clearly defined inputs, outputs, and permissions.



\---



## Frontend



The main frontend is responsible for presentation and interaction.



Rules:



* Do not import Python/backend internals.
* Do not implement authoritative gameplay calculations in frontend code if the backend already owns them.
* Use the existing HTTP/API contract.
* Keep UI state separate from persistent backend state.
* Do not introduce a second communication mechanism merely for convenience.



\---



## Plugins (first-party plugin system)



The root `plugins/` directory is the first-party plugin location: projects
under different licenses or other complete features are shipped as plugins,
detected and loaded at startup by convention (a directory with an entry file
is mounted/enabled). **First-party only — no third-party plugin interface or
specification.** The current plugin is the 3D replay viewer (Local-ChroViewer),
developed as an independent external GPL-2.0 project whose build output is
loaded from `plugins/chro/` (single detection path, no fallback).



Treat it as an isolated subsystem.



Rules:



* Do not casually merge a plugin's architecture into the main frontend.
* Do not add SaberLab business logic directly into a plugin unless the feature explicitly belongs there.
* Preserve each plugin's independent build process.
* Communicate through stable interfaces/contracts (HTTP mounts + iframe + query parameters).
* Do not copy a plugin's GPL-2.0-only source into the GPL-3.0-or-later repository.
* Never add a fallback path to local build artifacts (the dev environment must behave exactly like the user edition).



\---



# 3\. IPC and Communication



The established IPC mechanism is the **HTTP API**.



Frontend/backend communication must use the documented API.



Do NOT:



* import backend Python modules into frontend code
* create ad-hoc filesystem communication
* introduce hidden local sockets
* duplicate business logic in the frontend to avoid an API
* bypass an existing API contract without architectural justification



If an existing API is insufficient, extend the API properly.



\---



# 4\. Configuration



Configuration is schema-driven.



When adding a user-configurable setting:



1. Add it to the canonical configuration schema.
2. Provide a sensible default.
3. Expose it through the existing configuration mechanism.
4. Update the frontend if user-facing.
5. Preserve backward compatibility where practical.



Do NOT:



* hardcode user settings in random frontend files
* create one-off `localStorage` settings when the backend configuration system already owns the setting
* duplicate the same setting in multiple unrelated schemas



\---



# 5\. Feature Development Rules



Before implementing a new feature, answer these questions:



```text

1. What problem does the feature solve?

2. Which architectural layer owns it?

3. What existing service/API/module should it reuse?

4. What data does it consume?

5. What data does it produce?

6. Does it affect persistence?

7. Does it affect backward compatibility?

8. Does it require network access?

9. Does it require changes to the frontend API contract?

10. What existing functionality could this change accidentally break?

```



Do not begin by creating a new module merely because the existing structure looks inconvenient.



Reuse existing abstractions first.



\---



# 6\. Implementation Quality Policy



Correct functionality comes first — "minimal" is a means to protect the existing system, never an excuse to ship a worse solution.



When implementing a feature:



* write precise, clean code that fully fulfills the requirement
* weigh efficiency and safety: never degrade performance, data integrity, or result quality merely to keep the diff small
* keep the change surface as small as practical — touch only what the feature needs
* reuse existing utilities and APIs where they genuinely fit
* avoid unrelated refactors, mass renaming, and stylistic rewrites of working modules



Do NOT make unrelated cleanup changes in the same task.



A feature request is not automatically permission to refactor the entire codebase.



\---



# 7\. Refactoring Rules



Large refactors require explicit justification.



Before a structural refactor:



1. Identify the current dependency graph.
2. Identify all affected modules.
3. Check the existing architecture documentation.
4. Preserve public/internal contracts where practical.
5. Split the refactor into small, verifiable changes.



Never perform a broad rewrite merely because a model believes the new architecture is "cleaner".



The existing architecture may encode important historical constraints and bug fixes.



\---



# 8\. Bug Fix Rules



When fixing a bug:



1. Reproduce or understand the failure.
2. Identify the root cause.
3. Make a precise fix with the smallest safe impact.
4. Avoid changing unrelated behavior.
5. Check whether the bug reveals a missing architectural rule.
6. Update documentation/tests when appropriate.



If a bug is caused by an existing architectural workaround, preserve the workaround unless its replacement has been verified.



\---



# 9\. UI Development Rules



SaberLab's visual design is part of the product.



When modifying UI:



* preserve established visual language
* preserve acrylic/glass/motion conventions unless redesign is intentional
* avoid unnecessary component rewrites
* preserve responsive behavior
* preserve existing data/interaction flows



Do not simplify a complex UI merely because a simpler implementation is easier.



When a visual behavior depends on a specific JS/CSS/data contract, inspect the entire data flow before changing it.



\---



# 10\. Data and Analysis Integrity



Never silently change the meaning of an existing metric.



For an existing analysis metric:



* preserve units
* preserve sign conventions
* preserve coordinate conventions
* preserve timing conventions
* preserve normalization rules
* preserve naming unless a deliberate breaking change is required



When changing an algorithm:



1. Explain why.
2. Identify affected metrics.
3. Compare old and new results where practical.
4. Update tests.
5. Document the behavioral change.



Numerically "better-looking" output is not sufficient justification for changing an established metric.



\---



# 11\. AI Development Rules



SaberLab may use LLMs extensively, but AI must remain controlled.



## AI is an interpreter, not the authority



Correct:



```text

Replay

→ Analysis

→ Structured facts

→ AI

→ Explanation

```



Incorrect:



```text

Replay

→ AI guesses what happened

```



## AI tool calls must be explicit



Future Agent tools should define:



* tool name
* purpose
* allowed inputs
* output schema
* read/write permissions
* failure behavior



Prefer read-only tools wherever possible.



Destructive or persistent operations require explicit authorization.



\---



# 12\. Agent Workspace Rules



For future SaberLab AI Agent functionality:



The Agent may inspect and analyze application data through approved tools.



The Agent must NOT gain unrestricted access to:



* arbitrary filesystem locations
* arbitrary process execution
* arbitrary network endpoints
* database internals
* original replay modification
* application configuration mutation



unless that capability is explicitly part of the approved tool contract.



The Agent workspace should behave like a controlled laboratory:



```text

Observe

 ↓

Analyze

 ↓

Hypothesize

 ↓

Experiment

 ↓

Measure

 ↓

Compare

 ↓

Conclude

```



The Agent must distinguish:



* observed facts
* calculated metrics
* model predictions
* hypotheses
* recommendations



Do not present predictions or hypotheses as measured facts.



\---



# 13\. External Resource Integration



SaberLab may integrate or port functionality from external open-source projects.



Before integrating external code:



1. Check the original repository license.
2. Confirm license compatibility.
3. Preserve required notices.
4. Document the source.
5. Keep imported/ported code isolated where practical.
6. Do not silently copy incompatible code.
7. Preserve attribution requirements.



When a subsystem is derived from another project, do not erase its provenance merely to make the code appear native.



\---



# 14\. Dependency Rules



Do not introduce a dependency for a trivial feature when the standard library or existing project utilities are sufficient.



Before adding a dependency, consider:



* maintenance status
* license
* package size
* platform compatibility
* build complexity
* offline behavior
* security implications
* whether equivalent functionality already exists



Prefer existing project dependencies over introducing another library for convenience.



\---



# 15\. Testing and Verification



After making code changes:



1. Run relevant tests.
2. Run syntax/type/build checks where applicable.
3. Verify affected HTTP endpoints.
4. Verify affected UI flows.
5. Confirm no unrelated functionality regressed.
6. Confirm no architecture boundary was bypassed.



For deterministic analysis, test with known replay/map inputs when practical.



For UI changes, verify both:



* normal state
* empty/loading/error state



For network-dependent functionality, test failure paths as well as successful responses.



\---



# 16\. Documentation Rules



Update documentation when a change affects:



* architecture
* public behavior
* configuration
* API contracts
* installation
* supported platforms
* external integrations
* user-visible functionality



Do not update documentation merely to make the diff look larger.



Documentation should describe actual behavior, not planned behavior, unless explicitly marked as a roadmap.



\---



# 17\. Changelog Rules



User-visible changes belong in the changelog according to the repository's existing convention.



Do not add changelog entries for every minor internal code edit.



Prefer meaningful categories such as:



* Added
* Changed
* Fixed
* Performance
* Compatibility
* Architecture



\---



# 18\. Versioning



Respect the existing project versioning strategy.



Do not arbitrarily change the version number during a feature implementation unless the task explicitly requests a release/version update.



A feature implementation and a release are separate operations.



\---



# 19\. Error Handling



Do not hide errors merely to make the UI appear successful.



Prefer:



```text

Expected failure

→ structured error

→ user-visible explanation

→ safe fallback

```



Avoid:



```text

Exception

→ silently ignored

→ fake success

```



Especially for:



* Replay parsing
* scoring
* database operations
* network requests
* AI calls
* cache operations



A failed external service must not silently overwrite valid local data.



\---



# 20\. Security and Privacy



SaberLab is local-first and may process personal gameplay data.



Never:



* log API keys
* expose tokens to frontend code unnecessarily
* upload replay data without an explicit feature requiring it
* transmit local files to third parties implicitly
* include secrets in source control



AI providers must receive only the data required for the requested operation.



\---



# 21\. Do Not Break These Invariants



The following invariants are considered architectural guardrails:



```text

1. Original Replay files remain read-only.

2. Deterministic analysis does not depend on an LLM.

3. Frontend does not directly import backend internals.

4. HTTP API remains the primary frontend/backend IPC boundary.

5. Database access remains centralized through the established abstraction.

6. Configuration remains schema-driven.

7. Plugins remain independently buildable and loadable (no fallback to local build artifacts).

8. External services remain optional for core local functionality.

9. AI does not become the source of truth for numerical metrics.

10. New functionality should extend the existing architecture instead of silently replacing it.

```



Breaking one of these invariants requires explicit architectural justification.



\---



# 22\. Working With Existing Documentation



Before making substantial changes, inspect:



* `README.md`
* `docs/DEVELOPMENT.md`
* `docs/HANDOFF.md`（Document for handoff, read first before first work.）
* `docs/CHANGELOG.md`
* relevant source modules
* existing tests



Documentation is part of the project's architecture.



Do not assume the directory structure alone explains the system.



Historical notes in development documentation may describe deliberate workarounds that must not be removed casually.



\---



# 23\. When Requirements Are Ambiguous



Do not invent a major architectural decision silently.



If a request can be implemented in multiple materially different ways:



1. Prefer the existing architecture.
2. Prefer a precise implementation on the existing architecture with the smallest practical impact.
3. Preserve existing behavior.
4. Ask for clarification before introducing a new architectural paradigm when necessary.



Do not replace the project's technology stack simply because another technology is fashionable.



\---



# 24\. Completion Checklist



Before considering a task complete, verify:



```text

\\\\\\\\\\\\\\\[ ] The implementation solves the requested problem.

\\\\\\\\\\\\\\\[ ] Existing architecture is preserved.

\\\\\\\\\\\\\\\[ ] No unnecessary files/modules were introduced.

\\\\\\\\\\\\\\\[ ] No unrelated behavior was changed.

\\\\\\\\\\\\\\\[ ] Original replay data remains untouched.

\\\\\\\\\\\\\\\[ ] Deterministic analysis remains deterministic.

\\\\\\\\\\\\\\\[ ] API boundaries remain intact.

\\\\\\\\\\\\\\\[ ] Database/config conventions remain intact.

\\\\\\\\\\\\\\\[ ] Plugin boundaries remain intact.

\\\\\\\\\\\\\\\[ ] Relevant tests/checks pass.

\\\\\\\\\\\\\\\[ ] User-visible behavior is documented when necessary.

\\\\\\\\\\\\\\\[ ] No secrets or sensitive local data were added.

```



\---



# 25\. Final Rule



When choosing between:



```text

A) a clever, invasive, "cleaner" rewrite

B) a minimal, compatible patch

```



Weigh the trade-off against the feature's real requirements:

* Functionality first: the implementation must correctly and completely solve the problem.
* Do not choose B merely to keep the diff small if it sacrifices efficiency, safety, or result quality.
* Do not choose A merely because it looks "cleaner" if the existing architecture already fits.



SaberLab is a growing system.



Its architecture, data contracts, deterministic analysis, and accumulated historical fixes are more valuable than any single implementation shortcut — and a precise implementation that respects them is usually also the better engineering.



> \\\\\\\\\\\\\\\*\\\\\\\\\\\\\\\*Build on SaberLab. Do not fight SaberLab.\\\\\\\\\\\\\\\*\\\\\\\\\\\\\\\*

