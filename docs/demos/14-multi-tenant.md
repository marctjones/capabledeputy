# Demo 14: Multi-Tenant Household

**Audience:** people deploying to a household. Alice and Bob share
the install but want their compartments separate.
**Time:** ~2 minutes.
**Requires:** nothing beyond `uv sync`.

The naive single-user policy treats `confidential.health` as a single
label. In a household, Alice's health data and Bob's health data are
in separate compartments. CapableDeputy's `Tenant` value class scopes
labels per principal: a `(Label, Tenant)` pair is the unit the policy
engine reasons about.

## What the demo proves

1. Alice's `confidential.health@alice` does NOT fire
   `health-meets-egress` when Bob is the actor under his own tenant.
2. The same labels in Alice's compartment DO fire when she's the
   actor.
3. Without a target-tenant scope, the engine checks every tenant
   present and the strictest decision wins (DENY > REQUIRE_APPROVAL >
   ALLOW).
4. The `MultiTenantDecision` exposes per-tenant decisions for audit —
   reviewers see *why* the overall decision came out the way it did.

## Walkthrough

```bash
uv run pytest tests/test_e2e_multi_tenant.py -v
```

### Per-tenant scoping

```python
alice = Tenant(id="alice", display_name="Alice")
bob = Tenant(id="bob", display_name="Bob")

tls = frozenset({
    TenantLabel(Label.CONFIDENTIAL_HEALTH, alice),  # only Alice
})

# Bob attempts an email; his compartment is clean.
decision = decide_multi_tenant(tls, caps, action, target_tenant=bob)
assert decision.decision == Decision.ALLOW

# Alice attempts the same; her compartment fires the rule.
decision = decide_multi_tenant(tls, caps, action, target_tenant=alice)
assert decision.decision == Decision.DENY
assert decision.rule == "health-meets-egress"
```

### Strictness order

When the action isn't scoped to one tenant, every tenant's compartment
is consulted. The strictest decision wins:

```python
# Alice has financial → financial-meets-purchase = REQUIRE_APPROVAL
# Bob has health      → health-meets-egress     = DENY
# Overall: DENY
decision = decide_multi_tenant(tls, caps, action)
assert decision.decision == Decision.DENY
```

This is correct behaviour: a purchase on the household's behalf
shouldn't proceed if Bob's compartment would block it.

### Backward-compat

Single-user installs that never set a non-default tenant behave
identically to v0.3. `Tenant.default()` is the implicit tenant for
ungated `Label` sets.

## Files

- `src/capabledeputy/policy/tenancy.py` — `Tenant`, `TenantLabel`,
  helpers
- `src/capabledeputy/policy/multi_tenant_engine.py` —
  `decide_multi_tenant`
- `tests/test_e2e_multi_tenant.py`
