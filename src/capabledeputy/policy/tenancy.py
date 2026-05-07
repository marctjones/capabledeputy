"""Multi-tenant labels for household deployments (v0.4 additive).

Until v0.4 every CapableDeputy install assumed a single user. For
households we want per-person label spaces so Alice's
`confidential.health` and Bob's `confidential.health` are *different*
labels: a session reading Alice's data must not be able to send to a
recipient that operates over Bob's data without an explicit
declassification.

This module is **additive**: the existing `Label` enum stays unchanged.
A new `Tenant` value class plus `TenantLabel` (the pair) lets policy
checks reason about (Label, Tenant) as the unit. Single-user installs
that never set a non-default tenant behave identically to v0.3.

Conflict-rule firing is per-tenant: a rule that triggers on
`confidential.health@alice` does NOT fire when only
`confidential.health@bob` is in scope, because the two labels belong
to disjoint compartments. That's the entire mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from capabledeputy.policy.labels import Label

DEFAULT_TENANT = "default"


@dataclass(frozen=True, order=True)
class Tenant:
    """A household member or trust principal. The id is stable per user
    (e.g. a UUID or a memorable handle); the display name is for UI."""

    id: str
    display_name: str = ""

    @classmethod
    def default(cls) -> Tenant:
        return cls(id=DEFAULT_TENANT, display_name="default")


@dataclass(frozen=True, order=True)
class TenantLabel:
    """The (Label, Tenant) pair. Hashable, immutable, comparable."""

    label: Label
    tenant: Tenant = field(default_factory=Tenant.default)

    def matches(self, other: TenantLabel) -> bool:
        return self.label == other.label and self.tenant == other.tenant

    def __str__(self) -> str:
        if self.tenant.id == DEFAULT_TENANT:
            return self.label.value
        return f"{self.label.value}@{self.tenant.id}"


def lift(label_set: frozenset[Label], tenant: Tenant | None = None) -> frozenset[TenantLabel]:
    """Lift a flat Label frozenset into a TenantLabel set under a tenant
    (default tenant if None). The inverse for single-tenant code paths.
    """
    t = tenant or Tenant.default()
    return frozenset(TenantLabel(label=label, tenant=t) for label in label_set)


def project(tenant_label_set: frozenset[TenantLabel]) -> frozenset[Label]:
    """Project a TenantLabel set down to its Label component (loses
    tenant info). Use ONLY for legacy single-tenant decision paths.
    """
    return frozenset(tl.label for tl in tenant_label_set)


def tenants_in(tenant_label_set: frozenset[TenantLabel]) -> frozenset[Tenant]:
    return frozenset(tl.tenant for tl in tenant_label_set)


def labels_for_tenant(
    tenant_label_set: frozenset[TenantLabel],
    tenant: Tenant,
) -> frozenset[Label]:
    """All Labels the session carries scoped to one tenant."""
    return frozenset(tl.label for tl in tenant_label_set if tl.tenant == tenant)
