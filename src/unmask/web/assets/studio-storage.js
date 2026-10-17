"use strict";

function openDatabase() {
  return new Promise((resolve, reject) => {
    if (!window.indexedDB) {
      reject(new Error("IndexedDB is unavailable"));
      return;
    }
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(STUDY_STORE)) {
        database.createObjectStore(STUDY_STORE, {keyPath: "id"});
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("Could not open local storage"));
  });
}

function storageRequest(mode, action) {
  return new Promise((resolve, reject) => {
    if (!state.database) {
      resolve(undefined);
      return;
    }
    const transaction = state.database.transaction(STUDY_STORE, mode);
    const store = transaction.objectStore(STUDY_STORE);
    const request = action(store);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("Local storage failed"));
  });
}

async function loadImports() {
  const records = await storageRequest("readonly", (store) => store.getAll());
  const storedRecords = Array.isArray(records)
    ? records.filter(
      (record) => record
        && typeof record.id === "string"
        && typeof record.updatedAt === "string",
    )
    : [];
  state.sampleOverrides = new Map(
    storedRecords
      .filter(
        (record) => record.recordType === "sample_override"
          && typeof record.sampleId === "string"
          && typeof record.title === "string"
          && typeof record.deleted === "boolean",
      )
      .map((record) => [record.sampleId, record]),
  );
  state.samples = state.samples
    .filter((sample) => !state.sampleOverrides.get(sample.id)?.deleted)
    .map((sample) => ({
      ...sample,
      title: state.sampleOverrides.get(sample.id)?.title || sample.title,
    }));
  state.imports = storedRecords
      .filter((study) => study.recordType !== "sample_override" && !study.bundled)
      .map(UnmaskAudit.normalize)
      .filter(Boolean)
      .map((study) => ({
        ...study,
        settings: {
          ...defaultStudySettings(),
          ...(study.settings || {}),
        },
        incidents: study.incidents || null,
      }))
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

async function saveRecord(record) {
  if (!state.storageAvailable) return;
  try {
    await storageRequest("readwrite", (store) => store.put(record));
  } catch (error) {
    state.storageAvailable = false;
    elements.storageStatus.textContent = "Local persistence unavailable";
    setNotice(`The study remains available for this session. ${error.message}`);
  }
}

async function saveStudy(study) {
  await saveRecord(study);
}

async function deleteStoredStudy(id) {
  await storageRequest("readwrite", (store) => store.delete(id));
}
