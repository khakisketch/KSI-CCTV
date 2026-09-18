import { describe, expect, it } from "vitest";

import { minRoleForPath, ROLE_RANK, roleAtLeast } from "@/lib/roles";

describe("ROLE_RANK", () => {
  it("orders admin above operator above viewer", () => {
    expect(ROLE_RANK).toEqual({ admin: 3, operator: 2, viewer: 1 });
  });
});

describe("roleAtLeast", () => {
  it("returns true for equal roles", () => {
    expect(roleAtLeast("viewer", "viewer")).toBe(true);
    expect(roleAtLeast("operator", "operator")).toBe(true);
    expect(roleAtLeast("admin", "admin")).toBe(true);
  });

  it("returns true when the role outranks the minimum", () => {
    expect(roleAtLeast("operator", "viewer")).toBe(true);
    expect(roleAtLeast("admin", "operator")).toBe(true);
    expect(roleAtLeast("admin", "viewer")).toBe(true);
  });

  it("returns false when the role is below the minimum", () => {
    expect(roleAtLeast("viewer", "operator")).toBe(false);
    expect(roleAtLeast("viewer", "admin")).toBe(false);
    expect(roleAtLeast("operator", "admin")).toBe(false);
  });
});

describe("minRoleForPath public paths", () => {
  it("returns null for login and auth paths", () => {
    expect(minRoleForPath("/login", "GET")).toBeNull();
    expect(minRoleForPath("/login/callback", "GET")).toBeNull();
    expect(minRoleForPath("/api/auth/login", "POST")).toBeNull();
    expect(minRoleForPath("/api/auth/session", "GET")).toBeNull();
  });
});

describe("minRoleForPath admin paths", () => {
  it("requires admin for user/model/system/config/policy paths", () => {
    expect(minRoleForPath("/api/users", "GET")).toBe("admin");
    expect(minRoleForPath("/api/users/abc", "GET")).toBe("admin");
    expect(minRoleForPath("/api/models", "GET")).toBe("admin");
    expect(minRoleForPath("/api/system/status", "GET")).toBe("admin");
    expect(minRoleForPath("/api/config", "PUT")).toBe("admin");
    expect(minRoleForPath("/api/policies", "GET")).toBe("admin");
  });

  it("requires admin for catalog/commissioning/video/preflight/support paths", () => {
    expect(minRoleForPath("/api/event-catalog", "GET")).toBe("admin");
    expect(minRoleForPath("/api/commissioning/report", "GET")).toBe("admin");
    expect(minRoleForPath("/api/video-sources/1", "DELETE")).toBe("admin");
    expect(minRoleForPath("/api/preflight", "POST")).toBe("admin");
    expect(minRoleForPath("/api/support/ticket", "POST")).toBe("admin");
  });

  it("requires admin for audit/alert/control/settings paths including settings pages", () => {
    expect(minRoleForPath("/api/audit", "GET")).toBe("admin");
    expect(minRoleForPath("/api/alert-preferences", "PUT")).toBe("admin");
    expect(minRoleForPath("/api/alerts/123", "GET")).toBe("admin");
    expect(minRoleForPath("/api/control/ptz", "POST")).toBe("admin");
    expect(minRoleForPath("/api/settings", "GET")).toBe("admin");
    expect(minRoleForPath("/settings", "GET")).toBe("admin");
    expect(minRoleForPath("/settings/users", "GET")).toBe("admin");
  });
});

describe("minRoleForPath operator paths", () => {
  it("requires operator for camera mutations but viewer for camera reads", () => {
    expect(minRoleForPath("/api/cameras/1", "POST")).toBe("operator");
    expect(minRoleForPath("/api/cameras/1", "PUT")).toBe("operator");
    expect(minRoleForPath("/api/cameras/1", "PATCH")).toBe("operator");
    expect(minRoleForPath("/api/cameras/1", "DELETE")).toBe("operator");
    expect(minRoleForPath("/api/cameras/1", "GET")).toBe("viewer");
    expect(minRoleForPath("/api/cameras", "GET")).toBe("viewer");
  });

  it("requires operator for incident actions and onvif paths", () => {
    expect(minRoleForPath("/api/incidents/abc/actions", "POST")).toBe("operator");
    expect(minRoleForPath("/api/incidents/abc/actions/ack", "POST")).toBe("operator");
    expect(minRoleForPath("/api/incidents/abc/actions", "GET")).toBe("operator");
    expect(minRoleForPath("/api/onvif/discover", "POST")).toBe("operator");
    expect(minRoleForPath("/api/onvif/1/snapshot", "GET")).toBe("operator");
  });
});

describe("minRoleForPath viewer fallback", () => {
  it("requires viewer for all other authenticated paths", () => {
    expect(minRoleForPath("/api/cameras", "POST")).toBe("operator");
    expect(minRoleForPath("/api/events", "GET")).toBe("viewer");
    expect(minRoleForPath("/api/incidents", "GET")).toBe("viewer");
    expect(minRoleForPath("/", "GET")).toBe("viewer");
    expect(minRoleForPath("/cameras", "GET")).toBe("viewer");
  });

  it("defaults to GET semantics without a method", () => {
    expect(minRoleForPath("/api/cameras/1")).toBe("viewer");
    expect(minRoleForPath("/api/models")).toBe("admin");
    expect(minRoleForPath("/login")).toBeNull();
  });
});

describe("minRoleForPath unregistered /api/* fail-closed (F2-M3)", () => {
  it("keeps viewer for unregistered /api/* reads", () => {
    expect(minRoleForPath("/api/unknown", "GET")).toBe("viewer");
    expect(minRoleForPath("/api/unknown/nested", "GET")).toBe("viewer");
    expect(minRoleForPath("/api/unknown")).toBe("viewer");
  });

  it("requires admin for unregistered /api/* mutations", () => {
    for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
      expect(minRoleForPath("/api/unknown", method)).toBe("admin");
      expect(minRoleForPath("/api/unknown/nested", method)).toBe("admin");
    }
  });

  it("keeps existing matched rules unchanged", () => {
    expect(minRoleForPath("/api/users", "GET")).toBe("admin");
    expect(minRoleForPath("/api/models", "POST")).toBe("admin");
    expect(minRoleForPath("/api/cameras/1", "POST")).toBe("operator");
    expect(minRoleForPath("/api/cameras/1", "GET")).toBe("viewer");
    expect(minRoleForPath("/api/events", "GET")).toBe("viewer");
    expect(minRoleForPath("/api/onvif/discover", "POST")).toBe("operator");
  });
});
