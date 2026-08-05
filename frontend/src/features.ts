/**
 * Presentation feature flags — temporary UI switches, flip and rebuild.
 *
 * SHOW_PAM = false hides every Privileged-Access-Management surface
 * (Admin → PAM tab, the locked PAM notification category, PAM inbox items,
 * the Governance "cyberark" health tile) for the final presentation: the
 * owner doesn't want to field questions about an unfamiliar subsystem.
 * The backend API is untouched — set back to true to restore everything.
 * Tests assert the flag-OFF state by default; Admin.test.tsx mocks this
 * module with SHOW_PAM=true to keep the PAM tab flow covered.
 */
export const SHOW_PAM = false;

/**
 * Roles a user may self-request in the Access Request form. The privileged
 * administrator roles (System Administrator, Security Administrator) are
 * excluded — those are provisioned through privileged flows (break-glass /
 * security admin), never self-service (owner decision 2026-08-05: "keep the
 * dropdown in line"). Set to null to offer the full catalog again.
 */
export const REQUESTABLE_ROLES: string[] | null = [
  "Trader",
  "Client",
  "Operations Analyst",
  "Risk & Compliance",
  "Approver",
  "Auditor",
];
