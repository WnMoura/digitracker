const DB_NAME = "digitracker-companion-v2";
const DB_VERSION = 1;
const clone = (value) => globalThis.structuredClone ? structuredClone(value) : JSON.parse(JSON.stringify(value));

class MemoryStorage {
  constructor(available = false) {
    this.available = available;
    this.presentation = new Map();
    this.operations = new Map();
  }

  async getPresentation(namespace) { return this.presentation.get(namespace) || null; }
  async setPresentation(namespace, value) { this.presentation.set(namespace, clone(value)); }
  async enqueue(operation) { this.operations.set(operation.id, clone(operation)); }
  async pending(namespace) {
    return [...this.operations.values()].filter((item) => item.namespace === namespace && item.status !== "done")
      .sort((a, b) => a.createdAt - b.createdAt);
  }
  async update(id, changes) {
    const value = this.operations.get(id);
    if (value) this.operations.set(id, {...value, ...clone(changes)});
  }
  async remove(id) { this.operations.delete(id); }
}

class IndexedStorage {
  constructor(db) { this.db = db; this.available = true; }

  _request(request) {
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error("Falha no armazenamento do telefone."));
    });
  }

  _transaction(store, mode, callback) {
    return new Promise((resolve, reject) => {
      const transaction = this.db.transaction(store, mode);
      let result;
      try { result = callback(transaction.objectStore(store)); } catch (error) { reject(error); return; }
      transaction.oncomplete = () => resolve(result);
      transaction.onerror = () => reject(transaction.error || new Error("Falha no armazenamento do telefone."));
      transaction.onabort = () => reject(transaction.error || new Error("Operação local interrompida."));
    });
  }

  async getPresentation(namespace) {
    const value = await this._request(this.db.transaction("presentation").objectStore("presentation").get(namespace));
    return value?.value || null;
  }

  async setPresentation(namespace, value) {
    await this._transaction("presentation", "readwrite", (store) => store.put({namespace, value, updatedAt: Date.now()}));
  }

  async enqueue(operation) {
    await this._transaction("operations", "readwrite", (store) => store.put(operation));
  }

  async pending(namespace) {
    const values = await this._request(this.db.transaction("operations").objectStore("operations").getAll());
    return values.filter((item) => item.namespace === namespace && item.status !== "done")
      .sort((a, b) => a.createdAt - b.createdAt);
  }

  async update(id, changes) {
    await this._transaction("operations", "readwrite", (store) => {
      const request = store.get(id);
      request.onsuccess = () => { if (request.result) store.put({...request.result, ...changes}); };
    });
  }

  async remove(id) { await this._transaction("operations", "readwrite", (store) => store.delete(id)); }
}

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains("presentation")) db.createObjectStore("presentation", {keyPath: "namespace"});
      if (!db.objectStoreNames.contains("operations")) db.createObjectStore("operations", {keyPath: "id"});
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("IndexedDB indisponível."));
  });
}

export async function createStorage() {
  if (!globalThis.indexedDB) return new MemoryStorage(false);
  try { return new IndexedStorage(await openDatabase()); }
  catch (_) { return new MemoryStorage(false); }
}
