# Product design

The workspace keeps the question, evidence and output close together. Neutral surfaces and a restrained accent make the content the focus. An editorial empty state explains the promise and offers concrete product questions. Typography, whitespace and alignment establish hierarchy; no ornamental AI imagery or dashboard metrics are needed.

## Information architecture

The October 2026 polish uses warm ivory `#FAFAF7`, charcoal `#1F1F1F`, lavender `#8B7CF6` and sage `#9CAF88`, with darker foreground variants for controls. The transparent waveform/spark symbol is shared by the wordmark and favicon. `polish.css` refines the existing CSS system; no font downloads or animation library are needed. User messages align right and assistant messages left in chronological order.

A persistent sidebar contains new conversation, session history and library status. The header shows the selected provider and its configured model/availability. The center column is the conversation. A right pane alternates between evidence inspection and artifact viewing so the workspace does not grow into four competing columns.

## Interactions

- Initial startup displays the brand while session-list/readiness reads complete (ten-second deadline), then uses a 600 ms content entrance. There is no forced delay or splash replay on conversation changes. Failures expose connection recovery; this is not a readiness success claim.

- New conversation is initially local UI state; its persistent session is created when the first message is sent.
- Session history has independent contexts. Navigation is disabled during a response to avoid mixing streamed content between sessions.
- Retrieval and generation expose short progress labels. Provisional text streams incrementally; the final validated message replaces it. Private chain-of-thought is never displayed.
- Source controls show the number of distinct episodes. Cards include guest, title, exact excerpt, available publication date, original URL and provenance.
- Artifact creation opens its pane automatically. Preview/source tabs, copy, download, expand and close support inspection and reuse. Previously saved artifacts reopen from their chat cards.
- Model selection is explicit. Unavailable providers explain recovery. Local requests never silently become cloud requests.
- Errors preserve the user's request and offer retry. A stop control cancels the stream. Deleting a conversation has a concrete confirmation naming its scope.

## Responsive behavior

Desktop uses a 240–270px sidebar and a flexible reading column. Evidence/artifacts occupy roughly 43% when open. At intermediate widths the pane overlays the chat area. On phones the sidebar is a dismissible drawer and the artifact/evidence pane uses the available screen below the header. The composer remains reachable and horizontal document scrolling is constrained to appropriate content containers.

## Accessibility

Semantic buttons, landmarks, headings and labels support keyboard and assistive navigation. The composer uses Enter to send and Shift+Enter for a new line, while preserving IME composition. Progress has a restrained live region; tokens are not announced individually. Provider availability has text in addition to a status dot. Modals have focus containment and Escape dismissal. Focus outlines remain visible, text can wrap, and reduced-motion preferences disable animations. Browser tests check phone overflow; manual QA covers zoom, keyboard focus and reading order.

## Components and trade-offs

`App` owns the single workspace state machine; `Markdown`, `Sources`, and `ArtifactPane` encapsulate the important trust and rendering boundaries. `api.ts` owns fetch/error handling and the incremental SSE decoder. Small UI atoms stay inline unless reuse or independent behavior justifies another component. Tailwind is available alongside the explicit CSS design system. There are no external font or image network dependencies.
