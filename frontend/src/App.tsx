import { Home } from "./components/Home";
import { RunPage } from "./components/RunPage";
import { useRoute } from "./routes";

export function App() {
  const route = useRoute();
  return (
    <main className="page">
      <p className="brand">
        <a href="#/">Job assistant</a>
      </p>
      {route.page === "run" ? <RunPage key={route.runId} runId={route.runId} /> : <Home />}
    </main>
  );
}
