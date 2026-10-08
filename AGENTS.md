# StreamScale repository instructions

Read `STREAMSCALE_CODING_AGENT_CONTEXT.md` and the current `docs/milestones.md`
before milestone implementation. The user's latest requested scope takes precedence
over the original brief's initial M0/M1 stopping boundary.

The user requires one maintained entry point: `streamscale.sh`. Every integrated
milestone must be included in its `all` workflow, with appropriate Make targets,
acceptance checks and demo/inspection commands. Update its help/current milestone
range, `docs/run-project.md`, and README together. Verify the complete launcher
when new milestone steps change its behavior. Preserve earlier stages and artifacts.

Shutdown/resume commands must target only the `streamscale-local` Compose project
and kind nodes labelled `io.x-k8s.kind.cluster=streamscale`. Retain volumes, node
containers and run artifacts by default. Never use global Docker stop/prune or
delete project data as part of setup, checks, normal shutdown or verification.
Document any separately requested destructive cleanup explicitly.

Do not stop running user services solely to test shutdown documentation or code;
use scoped command tests unless a live shutdown test is explicitly authorized.
