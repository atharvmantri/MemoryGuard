import { useState } from "react";
import "./styles/tokens.css";
import "./styles/brand.css";
import Header from "./components/Header";
import MemoriesList from "./pages/MemoriesList";
import MemoryDetail from "./pages/MemoryDetail";
import QueryPlayground from "./pages/QueryPlayground";
import StoreStatus from "./pages/StoreStatus";
import ContextSync from "./pages/ContextSync";

type Tab = "memories" | "query" | "context" | "status";

const TABS: { id: Tab; label: string }[] = [
  { id: "memories", label: "Memories" },
  { id: "query", label: "Query playground" },
  { id: "context", label: "Context Sync" },
  { id: "status", label: "Store status" },
];

/**
 * Vault Mesh brand shell for the OSS local dashboard.
 *
 * Wires the read-focused pages (memories list, memory detail, query
 * playground, store status — Requirements 15.1–15.4) behind a tiny tab switch.
 * A dependency-light client-side nav (no router) keeps the OSS dashboard small.
 * When a memory is selected, the detail view replaces the active tab content
 * until the user navigates back.
 */
export function App() {
  const [tab, setTab] = useState<Tab>("memories");
  const [selectedMemoryId, setSelectedMemoryId] = useState<string | null>(null);

  const openMemory = (memoryId: string) => setSelectedMemoryId(memoryId);
  const closeMemory = () => setSelectedMemoryId(null);

  const selectTab = (next: Tab) => {
    setSelectedMemoryId(null);
    setTab(next);
  };

  return (
    <div className="mg-app">
      <Header mode="local" />

      <nav className="mg-tabs" aria-label="Dashboard sections">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className="mg-tab"
            aria-current={tab === t.id && !selectedMemoryId ? "page" : undefined}
            data-active={tab === t.id && !selectedMemoryId ? "true" : "false"}
            onClick={() => selectTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <main className="mg-main">
        {selectedMemoryId ? (
          <MemoryDetail
            memoryId={selectedMemoryId}
            onSelect={openMemory}
            onBack={closeMemory}
          />
        ) : (
          <>
            {tab === "memories" && <MemoriesList onSelect={openMemory} />}
            {tab === "query" && <QueryPlayground onSelect={openMemory} />}
            {tab === "context" && <ContextSync />}
            {tab === "status" && <StoreStatus />}
          </>
        )}
      </main>
    </div>
  );
}

export default App;
