# UI/UX Best Practices Reference

> Compiled from klub-io, ConvertChat, and building-websites-with-claude-code guides.

---

## Visual Hierarchy

### Tonal Layering Over Borders (from klub DESIGN.md)
- **"No-Line Rule"** — Define boundaries through background color shifts, not 1px grey borders
- Base layer: main surface color
- Secondary layer: slightly darker for sidebar/secondary zones
- Component layer: white for high-priority cards
- Creates sophisticated edge distinction without visual clutter

### Shadows
- Ambient shadows: 32px blur, 4% opacity, tinted (never pure black)
- Don't use shadows to "pop" — use them for "atmosphere"

### Typography Hierarchy
- Display layer: hooks and section headers (bold, large)
- Narrative layer: titles and body copy
- Metadata layer: labels, chips, micro-copy (smaller, lighter)

---

## Sidebar & Navigation

### Hierarchy in Sidebars
- Top-level items (like "Todos") sit flush left with more vertical padding
- Sub-items (folders, categories) are indented to show they're children
- Separator line between levels reinforces the hierarchy
- Inline "add new" button lives at the same indent as its siblings

### Responsive Breakpoints to Test
- 320px (smallest real phones, e.g., iPhone SE)
- 375px (iPhone standard)
- 390px (iPhone 14/15)
- 430px (iPhone Pro Max)
- 768px (iPad portrait — critical for navbar usability)
- 1024px (iPad landscape / small laptops)
- 1280px (standard desktop)

### Mobile Navigation
- Spanish text is 10-15% longer than English — test BOTH locales
- Move full-nav breakpoint from `md` to `lg` to avoid overflow at 768px
- Add `whitespace-nowrap` to buttons to prevent wrapping at 320px

---

## Destructive Action Safeguards

### Two-Stage Consent Pattern (from ConvertChat)
- Stage 1: User initiates action (click)
- Stage 2: User manually confirms (explicit dialog)
- A click does NOT equal commitment — always require confirmation for destructive ops

### Context-Aware Actions
- Show different actions based on context (folder view vs global view)
- Hide the most dangerous action when context makes it ambiguous
- Example: "Eliminar" (permanent delete) hidden in folder views; "Quitar de carpeta" (remove membership) shown instead

### Confirmation Dialog Severity Levels
- **Gentle**: "Se quitarán X contactos de esta carpeta. Seguirán existiendo en Todos."
- **Strong**: Warning box + "Esta accion no se puede deshacer" + explicit count of affected items

### State-Based Access Control
- Document every scenario (user type x context) and the result
- Matrix approach prevents edge cases from slipping through

---

## List & Folder UX

### Contact Lists
- Show relevant columns only — no per-item channel labels
- Simple stat display: icon + number (not bordered cards that look clickable)
- Summary breakdown in toolbar: "X contactos: Y por email, Z por WhatsApp"

### Dividers
- Forbid divider lines between list items — use whitespace or subtle background shifts
- Exception: separator between hierarchy levels (e.g., "Todos" vs folders) is OK

---

## Forms & Inputs

### Minimalist Input Fields (from klub DESIGN.md)
- Background color instead of full border
- 2px bottom-accent in brand color when focused
- Labels in lighter variant color

### Chips/Tags
- Pill-shaped (`rounded-full`) for tags
- Use secondary color for high legibility without visual aggression

---

## Buttons

### Style Rules
- Primary: Solid background, white text
- Secondary: Ghost style — no background, subtle border
- Tertiary: Text-only with brand color
- **Never use fully-rounded pill shape** for action buttons (reads as AI-generated/template)
- Use `rounded-xl` instead

---

## Copy & Messaging

### Banned Phrases (reads as AI-generated)
- "Seamless", "leverage", "unlock the power of", "cutting-edge", "revolutionize"
- Overline labels ("OUR FEATURES", "HOW IT WORKS")
- Em dashes in body copy

### Rules That Work
- Lead with outcomes, not features
- Use real numbers ("98% open rate") vs vague ("Better engagement")
- Write like explaining to a friend
- Every CTA should go somewhere real (no dead buttons)

---

## Animation & Motion

### Reduced Motion Requirement
```tsx
const shouldReduceMotion = useReducedMotion();
const [mounted, setMounted] = useState(false);
useEffect(() => setMounted(true), []);

if (!mounted || shouldReduceMotion) {
  return <StaticFinalState />;
}
```
- SSR: render static final state
- Client + reduced motion: render static final state
- Client + motion OK: render animations

---

## Mobile Type Scale

| Element | Mobile | Desktop |
|---------|--------|---------|
| H1 | 42px | 56-80px |
| H2 | 34px | 40-44px |
| Lead | 16px | 18px |
| Body | 14px | 14px |
| Card headings | 18px | 18px |
| Pill text | 12px | 14px |

**Critical insight:** Desktop scales collapse hierarchy on mobile — H1, leads, and pills appear near-equal size at 390px.

---

## Spacing Strategy

- Section padding: `px-6 py-28 md:py-36`
- Card internal padding: 2rem
- Vertical spacing between list items: 1rem
- **If layout feels crowded, increase spacing by two increments**

---

## Quick Reference Table

| Category | Rule | Why |
|----------|------|-----|
| Borders | No 1px grey lines | Kills premium feel; use tonal shifts |
| Shadows | Ambient 32px blur, 4% opacity | Atmosphere, not "pop" |
| Buttons | rounded-xl, never pill | Pill reads as AI-generated |
| Copy | No "seamless", "leverage" | AI-generated feel |
| Navigation | Test at 320/375/390/430/768px | Spanish 10-15% longer; overflow risk |
| Lists | Whitespace over divider lines | Cleaner visual hierarchy |
| Destructive | Two-stage consent | Click != commitment |
| Responsive | Reduce padding on mobile | Desktop spacing too generous on phones |
| Animations | Reduced motion fallback | Accessibility + SSR safety |
| Type Scale | Mobile H1 42px vs desktop 80px | Hierarchy collapses at 390px |
