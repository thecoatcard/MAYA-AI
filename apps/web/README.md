# MAYA web foundation

A Next.js 15 App Router scaffold using React 19, strict TypeScript, Tailwind CSS
and Lucide icons. The overview is a static server-rendered page with real section
navigation. Product cards are explicitly labelled planned; no chat or sign-in
behavior is simulated.

From the repository root:

```powershell
npm --prefix apps\web ci
npm --prefix apps\web run dev
npm --prefix apps\web run lint
npm --prefix apps\web run typecheck
npm --prefix apps\web test
npm --prefix apps\web run build
```

Development and production-start scripts bind to loopback. The page uses system
fonts and does not contact the backend, a model provider or an external font service.
No application environment variables are read by this scaffold.

Vitest and Testing Library cover the rendered page, roadmap states and navigation.
The [test strategy](../../docs/testing/FOUNDATION.md) describes the verification boundary.
