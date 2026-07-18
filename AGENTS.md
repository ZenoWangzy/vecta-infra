# AGENTS.md

Read `CONTRIBUTING.md` before non-trivial work. It defines this repository's
responsibility boundary with `vecta`, the delivery lanes, required validation,
and data-first migration rules.

The VectA contribution lifecycle is mandatory: topic branch -> `develop` ->
vtest validation -> `main`. A verified production `hotfix/*` merged into VectA
`main` must return through `main -> develop`. Infrastructure workflows preserve
this contract and never turn a main image build into an automatic production
deployment.

Keep this file as the agent entry point. Put durable contribution and delivery
conventions in `CONTRIBUTING.md`, not generated indexes or repeated detail here.
