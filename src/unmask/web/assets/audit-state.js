"use strict";

// Versioned records keep replay cases distinct from legacy specification studies.
const UnmaskAudit = {
  normalize(study) {
    if (study.recordType === "judge_audit") {
      if (study.recordVersion !== 1 || typeof study.auditCaseText !== "string") return null;
      return {...study, validation: null, activeDocument: "protocol", auditView: "sources"};
    }
    if (study.recordType && study.recordType !== "specification") return null;
    if (typeof study.specificationText !== "string") return null;
    return {...study, recordType: "specification", recordVersion: 1};
  },
  cached(study, version) {
    const report = study.cachedRun;
    return report?.workflow === "audit_judge" && report.schemaVersion === 1
      && report.auditorVersion === version && report.contentHash === study.validation?.contentHash
      ? report : null;
  },
  input(text) {
    const parsed = JSON.parse(text);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("A replay case or report must be a JSON object.");
    }
    if (parsed.workflow === "audit_judge" && parsed.case) {
      return {text: JSON.stringify(parsed.case, null, 2), historical: true};
    }
    return {text, historical: false};
  },
  replace(study, text, name, validation) {
    return {...study, auditCaseText: text, auditCaseName: name, validation, cachedRun: null,
      activeDocument: "protocol", auditView: "sources", updatedAt: new Date().toISOString()};
  },
};
if (typeof module !== "undefined") module.exports = UnmaskAudit;
