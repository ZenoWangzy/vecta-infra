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

The VectA contribution lifecycle remains mandatory and is now a single trunk:
topic branch -> `main` -> required exact-SHA postsubmit validation. `main` is
VectA's only long-lived branch and a production `hotfix/*` is an ordinary topic
branch into it, with no return leg. Infrastructure workflows preserve the
production/myPC branch, runner, permission, and audit boundaries and never turn
a manual image build into an automatic production deployment.

Branch protection is unavailable on this plan (403 "Upgrade to GitHub Pro or
make this repository public") and the `production` environment has no protection
rules, so every control is a fail-closed check inside
`build-mypc-images.yml`. Its `build` job's `if:` — repository, event, and
`refs/heads/main` — is one of those controls, not boilerplate: the job runs on
`[self-hosted, mypc, prod-build]`, which is the production machine itself.
Never remove it.

Keep this file as the agent entry point. Put durable contribution and delivery
conventions in `CONTRIBUTING.md`, not generated indexes or repeated detail here.
