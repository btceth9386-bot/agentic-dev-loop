# Requirements Document

## Introduction

Agentic Loop is a local multi-agent CI/CD pipeline for macOS and Linux that autonomously handles GitHub Issues through implementation, review, and merge. The system uses GitHub CLI polling, label-based state management, per-issue git worktree isolation, and configurable agent roles — with human oversight preserved at issue validation checkpoints.

## Glossary

- **Dispatcher**: The main Python orchestrator process (`dispatcher.py`) responsible for polling GitHub, managing workspaces, assigning agents, writing state logs, and transitioning labels.
- **Coding_Agent**: An agent CLI (e.g., kiro-cli, codex) that implements code changes for a GitHub issue, commits, pushes, and opens a pull request.
- **Review_Agent**: An agent CLI (e.g., claude) that reviews pull requests and either approves or requests changes.
- **Todo_Label**: The `todo` GitHub label applied by a human to indicate that an issue has been organized, described, and is ready for the pipeline to pick up. Acts as a gate preventing raw or incomplete issues from entering the pipeline. This is the default Pickup_Label for the `coding` role.
- **Label_State_Machine**: The set of GitHub issue labels used as the single source of truth for issue state transitions.
- **Worktree**: A per-issue isolated git worktree directory created under `~/.agent-pipeline/<repo>/workspaces/`.
- **State_Log**: A YAML-formatted log file recording each state transition for an issue, stored under `~/.agent-pipeline/<repo>/state/`.
- **Lockfile**: A file under `/tmp/` used to track concurrent assignments per agent, supporting multiple locks up to the agent's `max_concurrent` limit.
- **Role_Capacity**: The number of issues the Dispatcher can pick up for a given role in a single poll cycle, calculated by summing `max_concurrent` across all agents with that role and subtracting currently occupied slots.
- **Pickup_Label**: A per-role field in the Agents_Config that specifies which GitHub label the Dispatcher should look for when polling issues for that role. For example, the `coding` role uses `todo` as its Pickup_Label, and the `review` role uses `pr-opened`.
- **Agents_Config**: The `agents.yml` configuration file defining agent names, roles, commands, concurrency limits, and cooldown settings. Each role definition includes a `pickup_label` field (the label the Dispatcher polls for), `label_on_start`, and `label_on_done`. Each agent's `command` field contains the complete CLI invocation including the AI agent's prompt/role assignment and any resume flags the user wants (e.g., `kiro-cli --resume --agent senior --no-interactive`). The Dispatcher runs the same `command` for both initial runs and retries, without constructing or modifying the invocation. All string values in the file support `${VAR_NAME}` environment variable expansion at load time.
- **Env_Var_Expansion**: The mechanism by which the Dispatcher substitutes `${VAR_NAME}` patterns in Agents_Config string values with the corresponding environment variable value from `os.environ` at config load time. Uses the regex pattern `\$\{[A-Za-z_][A-Za-z0-9_]*\}`.
- **Auto_Merge_Cronjob**: A separate shell script (`merge.sh`) that merges pull requests labeled `ready-to-merge`.
- **Notification_System**: The subsystem that sends messages via Telegram or Discord at key state transitions.
- **Issue_Context**: The `ISSUE.md` file written into a worktree containing the GitHub issue title, body, and comments, and optionally the associated PR details (number, URL, title, review comments) when a PR exists for the issue's branch.
- **PR_Context**: The pull request details (number, URL, title, review comments) fetched via the GitHub CLI for the branch `agent/issue-<number>`. Included in `ISSUE.md` when a PR exists.
- **Required_Gitignore_Entries**: The set of `.gitignore` patterns that must be present in the repository to prevent agents from committing dispatcher-injected files (`ISSUE.md`) and agent CLI session directories (`.kiro/`, `.claude/`, `.codex/`, `.copilot/`, `.gemini/`).

## Requirements

### Requirement 1: Issue Detection via Polling

**User Story:** As a pipeline operator, I want the Dispatcher to poll GitHub for new issues on a regular interval, so that new work is detected without requiring a public webhook endpoint.

#### Acceptance Criteria

1. THE Dispatcher SHALL poll the GitHub repository every 3 minutes using the GitHub CLI, checking each role's Pickup_Label to detect issues eligible for that role.
2. WHEN polling for the `coding` role, THE Dispatcher SHALL look for issues carrying the label specified by the coding role's `pickup_label` field in the Agents_Config (default: `todo`).
3. WHEN polling detects pending issues for a role, THE Dispatcher SHALL calculate the available capacity by summing `max_concurrent` across all agents with that role and subtracting currently occupied slots.
4. WHEN pending issues are detected for a role, THE Dispatcher SHALL pick up a number of issues equal to the lesser of the available capacity and the number of pending issues.
5. WHEN a new issue is picked up for a role, THE Dispatcher SHALL atomically remove the role's Pickup_Label and apply the role's `label_on_start` label to the issue before spawning any agent.
6. WHEN an issue already carries a state label other than a Pickup_Label, THE Dispatcher SHALL skip the issue during polling.
7. WHEN the available Role_Capacity for a role is zero, THE Dispatcher SHALL leave all pending issues for that role queued until the next poll cycle.
8. THE Dispatcher SHALL reload the Agents_Config file on every poll cycle.

### Requirement 2: Label State Machine

**User Story:** As a pipeline operator, I want issue state to be tracked entirely through GitHub labels, so that state is visible in the GitHub UI and survives Dispatcher restarts.

#### Acceptance Criteria

1. THE Label_State_Machine SHALL support the following states: `todo`, `in-progress`, `pr-opened`, `reviewing`, `changes-requested`, `ready-to-merge`, `human-review-required`.
2. THE `todo` state SHALL be the entry point of the Label_State_Machine, applied manually by a human to signal that an issue is ready for the pipeline.
3. WHEN the Dispatcher transitions an issue to a new state, THE Dispatcher SHALL remove the previous state label and apply the new state label atomically.
4. THE Label_State_Machine SHALL be the single source of truth for issue state, with no local database required.

### Requirement 3: Agent Configuration

**User Story:** As a pipeline operator, I want to configure agents and their roles in a YAML file, so that I can swap agent roles without code changes.

#### Acceptance Criteria

1. THE Dispatcher SHALL read agent definitions from the Agents_Config file, including name, role, command, max_concurrent, cooldown_minutes, and optional env fields.
2. THE Agents_Config `command` field SHALL contain the complete CLI invocation for the agent, including any prompt or role assignment flags and any resume flags the user desires (e.g., `--resume`), and THE Dispatcher SHALL execute the same command for both initial runs and retries without constructing or modifying the invocation.
3. WHEN the Agents_Config file is modified, THE Dispatcher SHALL pick up the changes on the next poll cycle without requiring a restart.
4. THE Agents_Config SHALL define `pickup_label`, `label_on_start`, and `label_on_done` for each role.
5. WHEN the Dispatcher loads the Agents_Config file, THE Dispatcher SHALL validate that each agent entry contains the required fields: name, role, command, and max_concurrent.
6. WHEN an agent definition includes an optional `env` map, THE Dispatcher SHALL merge those key-value pairs into the subprocess environment when spawning that agent, overriding any existing environment variables with the same name. This allows different agents to use separate credentials (e.g., distinct `GH_TOKEN` values for coder and reviewer accounts).
6. WHEN the Dispatcher loads the Agents_Config file, THE Dispatcher SHALL validate that each role definition contains the required fields: `pickup_label`, `label_on_start`, and `label_on_done`.
7. IF a required field is missing from an agent entry in the Agents_Config file, THEN THE Dispatcher SHALL fail immediately with a descriptive error message identifying the agent entry and the missing field.
8. IF a required field is missing from a role definition in the Agents_Config file, THEN THE Dispatcher SHALL fail immediately with a descriptive error message identifying the role and the missing field.
9. IF the Agents_Config file contains invalid YAML syntax, THEN THE Dispatcher SHALL fail immediately with a descriptive parse error message.
10. WHEN the Dispatcher loads the Agents_Config file, THE Dispatcher SHALL expand all `${VAR_NAME}` patterns in YAML string values by substituting the value of the corresponding environment variable from `os.environ`.
11. THE Dispatcher SHALL match environment variable references using the pattern `\$\{[A-Za-z_][A-Za-z0-9_]*\}` and SHALL leave strings without matching patterns unchanged.
12. IF a `${VAR_NAME}` pattern references an environment variable that is not set, THEN THE Dispatcher SHALL fail immediately with a descriptive error message identifying the undefined variable name and the config key where it was referenced.
13. WHEN the Dispatcher starts, THE Dispatcher SHALL verify that a `.gitignore` file exists at the configured `repo_path`.
14. IF the `.gitignore` file is missing at `repo_path`, THEN THE Dispatcher SHALL fail immediately with a descriptive error message stating that a `.gitignore` file is required.
15. WHEN the Dispatcher starts, THE Dispatcher SHALL validate that the `.gitignore` file contains entries for all Required_Gitignore_Entries: `ISSUE.md`, `.kiro/`, `.claude/`, `.codex/`, `.copilot/`, and `.gemini/`.
16. IF the `.gitignore` file is missing one or more Required_Gitignore_Entries, THEN THE Dispatcher SHALL fail immediately with a descriptive error message listing the missing entries.
17. WHEN the Dispatcher starts, THE Dispatcher SHALL check whether an `AGENTS.md` file exists at the configured `repo_path`.
18. IF `AGENTS.md` is missing at `repo_path`, THEN THE Dispatcher SHALL log a warning message "⚠️ AGENTS.md not found in repo_path — agent CLIs may lack project context." and continue execution without failing.

### Requirement 4: Agent Selection and Rotation

**User Story:** As a pipeline operator, I want the Dispatcher to select agents using round-robin rotation, so that token consumption is balanced across providers.

#### Acceptance Criteria

1. WHEN assigning work, THE Dispatcher SHALL filter agents by the required role.
2. WHEN multiple agents with the same role are available, THE Dispatcher SHALL select agents using round-robin rotation.
3. WHEN an agent has active Lockfiles equal to its `max_concurrent` setting, THE Dispatcher SHALL skip that agent during selection.
4. WHEN an agent has active Lockfiles fewer than its `max_concurrent` setting, THE Dispatcher SHALL consider that agent available for assignment.
5. WHEN an agent is in cooldown due to rate limiting, THE Dispatcher SHALL skip that agent and rotate to the next available agent with the same role.
6. WHEN agent rotation occurs due to rate limiting, THE Notification_System SHALL send a notification.
7. WHEN multiple issues are picked up in a single poll cycle, THE Dispatcher SHALL assign each issue to the next available agent slot using round-robin rotation across agents with remaining capacity.

### Requirement 5: Workspace Management via Git Worktree

**User Story:** As a pipeline operator, I want each issue to get an isolated git worktree, so that multiple coding agents can work on different issues in parallel without file conflicts.

#### Acceptance Criteria

1. WHEN a new issue is assigned, THE Dispatcher SHALL create a git worktree at `~/.agent-pipeline/<repo>/workspaces/issue-<number>` on branch `agent/issue-<number>` from `main`.
2. THE Dispatcher SHALL run the `git worktree add` command from inside the configured `repo_path`.
3. WHEN a worktree already exists for an issue, THE Dispatcher SHALL reuse the existing worktree.
4. WHEN a PR is merged and the issue is closed, THE Dispatcher SHALL remove the worktree, delete the branch, and remove the current symlink.
5. WHEN an issue reaches attempt 3 and is labeled `human-review-required`, THE Dispatcher SHALL clean up the workspace.
6. WHEN a human reopens a previously cleaned-up issue, THE Dispatcher SHALL recreate the worktree from `main` and continue the state log sequence from the next index.

### Requirement 6: Issue Context Injection

**User Story:** As a pipeline operator, I want the Dispatcher to write issue details into the workspace before running an agent, so that agent CLIs can read context from the working directory.

#### Acceptance Criteria

1. WHEN a workspace is prepared for an agent, THE Dispatcher SHALL write the issue title, body, and comments to an `ISSUE.md` file in the worktree root using the GitHub CLI.
2. THE Dispatcher SHALL write the Issue_Context before executing the agent CLI subprocess.
3. THE Dispatcher SHALL include the issue number prominently in the `ISSUE.md` file so that the Coding_Agent can reference it when creating a pull request title with a closing keyword (e.g., `Fix #<issue_number>: <description>`).
4. WHEN a pull request exists for the issue's branch (`agent/issue-<number>`), THE Dispatcher SHALL fetch PR details (number, URL, title, and review comments) via the GitHub CLI and include them in the `ISSUE.md` file.
5. THE Dispatcher SHALL re-write `ISSUE.md` (overwrite) before each agent invocation to ensure the file contains the latest issue details and PR_Context.
6. WHEN no pull request exists for the issue's branch, THE Dispatcher SHALL write `ISSUE.md` containing only the issue details without a PR section.

### Requirement 7: Coding Agent Execution

**User Story:** As a pipeline operator, I want coding agents to implement issues autonomously, so that code changes are committed, pushed, and a PR is opened without manual intervention.

#### Acceptance Criteria

1. WHEN an issue is labeled `in-progress`, THE Dispatcher SHALL spawn the selected Coding_Agent as a subprocess with `cwd` set to the issue worktree.
2. WHEN the Coding_Agent subprocess exits with code 0, OR a pull request exists on GitHub for the branch `agent/issue-<number>`, THE Dispatcher SHALL transition the issue label from `in-progress` to `pr-opened`.
3. IF the Coding_Agent subprocess exits with a non-zero code AND no pull request exists on GitHub for the branch `agent/issue-<number>`, THEN THE Dispatcher SHALL log the failure in the State_Log and send a notification.
4. THE Dispatcher SHALL capture stdout and stderr from the Coding_Agent subprocess using `capture_output=True`.
5. THE Coding_Agent SHALL include a closing keyword referencing the issue number in the pull request title using the format `Fix #<issue_number>: <description>`, so that merging the PR into the default branch automatically closes the linked GitHub issue.
6. WHEN the Dispatcher assigns a Coding_Agent to an issue, THE Dispatcher SHALL post a comment on the GitHub issue containing the agent name, role, and attempt number using the format `🤖 Assigned to **<agent_name>** (<role>) — attempt <N>`.

### Requirement 8: Review Agent Execution

**User Story:** As a pipeline operator, I want a review agent to autonomously review PRs, so that code quality is checked before merging.

#### Acceptance Criteria

1. WHEN an issue carries the label specified by the review role's `pickup_label` field in the Agents_Config (default: `pr-opened`), THE Dispatcher SHALL spawn the selected Review_Agent as a subprocess with `cwd` set to the issue worktree.
2. WHEN the Dispatcher spawns the Review_Agent, THE Dispatcher SHALL atomically remove the review role's Pickup_Label and apply the review role's `label_on_start` label (default: `reviewing`) to the issue.
3. WHEN the Review_Agent subprocess exits with code 0 AND the GitHub PR has at least one APPROVED review, THE Dispatcher SHALL transition the issue label from `reviewing` to `ready-to-merge`.
4. WHEN the Review_Agent subprocess exits with a non-zero code OR the PR does not have an APPROVED review, AND the PR has review comments or a CHANGES_REQUESTED review on GitHub, THE Dispatcher SHALL transition the issue label from `reviewing` to `changes-requested`.
5. THE Dispatcher SHALL capture stdout and stderr from the Review_Agent subprocess using `capture_output=True`.
6. WHEN the Dispatcher assigns a Review_Agent to an issue, THE Dispatcher SHALL post a comment on the GitHub issue containing the agent name, role, and attempt number using the format `🤖 Assigned to **<agent_name>** (<role>) — attempt <N>`.
7. IF the Review_Agent subprocess exits with a non-zero code AND no review comments or CHANGES_REQUESTED review exist on the PR, THE Dispatcher SHALL log a warning and skip the label transition.

### Requirement 9: Change Request Handling and Retry Loop

**User Story:** As a pipeline operator, I want the pipeline to automatically retry fixes when changes are requested, so that minor review feedback is resolved without human intervention.

#### Acceptance Criteria

1. WHEN an issue is labeled `changes-requested`, THE Dispatcher SHALL spawn the Coding_Agent using the agent's `command` in the same worktree.
2. WHEN the Coding_Agent completes fixes successfully, THE Dispatcher SHALL transition the issue label back to `pr-opened` for re-review.
3. THE Dispatcher SHALL derive the review attempt count for an issue by counting files matching the pattern `*-changes-requested.log` in the issue's state directory (`~/.agent-pipeline/<repo>/state/issue-<number>/`).
4. WHEN the number of `*-changes-requested.log` files in the issue's state directory reaches 3 without approval, THE Dispatcher SHALL transition the issue label to `human-review-required`.
5. WHEN an issue is labeled `human-review-required`, THE Dispatcher SHALL send a notification and clean up the workspace.
6. WHEN the Dispatcher assigns a Coding_Agent for a retry on a `changes-requested` issue, THE Dispatcher SHALL post a comment on the GitHub issue containing the agent name, role, and retry attempt number using the format `🤖 Assigned to **<agent_name>** (<role>) — retry attempt <N>`.

### Requirement 10: Lockfile-Based Concurrency Control

**User Story:** As a pipeline operator, I want lockfile-based concurrency control, so that the same agent is not assigned to multiple issues simultaneously beyond its configured limit.

#### Acceptance Criteria

1. WHEN an agent is assigned to an issue, THE Dispatcher SHALL create a Lockfile at `/tmp/agent-<name>-<issue_number>.lock` containing `<unix_timestamp>:<issue_number>`.
2. THE Dispatcher SHALL support multiple concurrent Lockfiles per agent, one per assigned issue, up to the agent's `max_concurrent` limit.
3. WHEN an agent completes its task for an issue, THE Dispatcher SHALL remove the Lockfile corresponding to that specific issue.
4. WHEN a Lockfile is older than 30 minutes, THE Dispatcher SHALL treat the Lockfile as stale and release it automatically.
5. THE Dispatcher SHALL count active (non-stale) Lockfiles per agent and compare against the `max_concurrent` setting to determine available slots.

### Requirement 11: State Logging

**User Story:** As a pipeline operator, I want every state transition logged to disk, so that I can observe and debug the pipeline history for any issue.

#### Acceptance Criteria

1. WHEN the Dispatcher transitions an issue to a new state, THE Dispatcher SHALL write a State_Log file at `~/.agent-pipeline/<repo>/state/issue-<number>/<index>-<state>.log`.
2. THE State_Log SHALL contain timestamp, issue number, agent name, role, previous state, current state, attempt count, stdout, and stderr in YAML format.
3. THE Dispatcher SHALL maintain a `current/issue-<number>` symlink pointing to the latest State_Log file.
4. WHEN a previously cleaned-up issue is reopened, THE Dispatcher SHALL continue the log index sequence from the next available number.
5. THE State_Log files SHALL serve as the source of truth for deriving the review attempt count, enabling the Dispatcher to count `*-changes-requested.log` files without requiring a separate counter file or in-memory tracking.

### Requirement 12: Auto-Merge Cronjob

**User Story:** As a pipeline operator, I want a separate cronjob to merge approved PRs, so that merging is decoupled from the Dispatcher process.

#### Acceptance Criteria

1. THE Auto_Merge_Cronjob SHALL run every 3 minutes, offset by 90 seconds from the Dispatcher poll cycle.
2. THE Auto_Merge_Cronjob SHALL list all PRs with the `ready-to-merge` label using the GitHub CLI.
3. WHEN a PR has the `ready-to-merge` label, THE Auto_Merge_Cronjob SHALL merge the PR using squash merge with `--auto` flag.
4. THE Auto_Merge_Cronjob SHALL rely on the pull request containing a closing keyword (e.g., `Fix #<issue_number>`) to trigger automatic GitHub issue closure upon merge into the default branch.

### Requirement 13: Notifications

**User Story:** As a pipeline operator, I want notifications at key state transitions, so that I am informed of important pipeline events without monitoring logs.

#### Acceptance Criteria

1. WHEN an issue transitions to `changes-requested`, `ready-to-merge`, or `human-review-required`, THE Notification_System SHALL send a notification.
2. WHERE Telegram is configured, THE Notification_System SHALL send messages via the Telegram Bot API using the configured token and chat ID.
3. WHERE Discord is configured, THE Notification_System SHALL send messages via the configured Discord webhook URL.
4. WHERE both Telegram and Discord are configured, THE Notification_System SHALL send to both channels.

### Requirement 14: Observability

**User Story:** As a pipeline operator, I want CLI-friendly observability, so that I can inspect the current state of all issues and the full history of any issue from the terminal.

#### Acceptance Criteria

1. THE Dispatcher SHALL maintain `current/` symlinks such that `ls -la ~/.agent-pipeline/<repo>/current/` shows the latest state of all active issues.
2. THE State_Log files SHALL be stored such that `ls ~/.agent-pipeline/<repo>/state/issue-<number>/` shows the full transition history.
3. THE Dispatcher SHALL write runtime logs to `/tmp/agentic-loop.log` and errors to `/tmp/agentic-loop.error.log`.

### Requirement 15: Crontab Scheduling

**User Story:** As a pipeline operator, I want the pipeline scheduled via crontab, so that it runs on both macOS and Linux without additional dependencies.

#### Acceptance Criteria

1. THE Dispatcher SHALL be executable as a crontab entry running every 3 minutes.
2. THE Auto_Merge_Cronjob SHALL be executable as a crontab entry running every 3 minutes with a 90-second sleep offset.
3. THE Dispatcher SHALL operate correctly as a stateless invocation, relying on GitHub labels and the filesystem for state persistence between runs.

### Requirement 16: Session Resume Support

**User Story:** As a pipeline operator, I want agent sessions to resume automatically, so that interrupted work continues without manual session management.

#### Acceptance Criteria

1. THE Dispatcher SHALL run agent CLIs with `cwd` set to the issue worktree directory, enabling CLI-native session resume from `.kiro/`, `.claude/`, or `.codex/` directories.
2. THE Dispatcher SHALL always run the same `command` for an agent regardless of whether the invocation is an initial run or a retry. Users SHALL include any resume flags (e.g., `--resume`) in the agent's `command` field in the Agents_Config if their CLI supports it.
3. THE Dispatcher SHALL not track or manage session IDs; session resume is delegated entirely to the agent CLI.
