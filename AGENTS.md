# AGENTS.md

Read `CONTRIBUTING.md` before non-trivial work. It defines this repository's
responsibility boundary with `vecta`, the delivery lanes, required validation,
and data-first migration rules.

vtest is permanently retired. Do not add or restore its workflow, inventory,
runner, deployment role, fallback, or compatibility wrapper. The VectA
caller-removal hotfix must merge first; only then may the matching `vecta-infra`
removal merge. `CONTRIBUTING.md` owns the detailed cross-repository sequence.

Production image builds are repository-writer-initiated, exact-SHA evidence
generated only by manual dispatch from `vecta-infra` main. The requested SHA
must be the current VectA main HEAD. The `production` environment is an audit
label without reviewer or protection gates. This evidence is independent of
VectA workflow execution and is not a production deployment or health claim.

The VectA contribution lifecycle remains mandatory: topic branch -> `develop`
-> required postsubmit validation -> `main`. A verified production `hotfix/*`
merged into VectA `main` must return through `main -> develop`. Infrastructure
workflows preserve the production/myPC branch, runner, permission, and audit
boundaries and never turn a manual image build into an automatic production
deployment.

Keep this file as the agent entry point. Put durable contribution and delivery
conventions in `CONTRIBUTING.md`, not generated indexes or repeated detail here.
