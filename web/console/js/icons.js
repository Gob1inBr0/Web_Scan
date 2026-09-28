// Inline SVG icon set (Lucide-style, stroke-based). Usage: <icon name="play" />

const PATHS = {
  "dashboard": `M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z`,
  "plus": `M12 5v14M5 12h14`,
  "play": `M6 4l14 8-14 8z`,
  "list": `M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01`,
  "layers": `M12 2 2 7l10 5 10-5zM2 17l10 5 10-5M2 12l10 5 10-5`,
  "box": `M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z M3.3 7 12 12l8.7-5M12 22V12`,
  "settings": `M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0z`,
  "bell": `M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9 M10.3 21a1.94 1.94 0 0 0 3.4 0`,
  "refresh": `M21 12a9 9 0 1 1-2.64-6.36 M21 3v6h-6`,
  "check": `M20 6 9 17l-5-5`,
  "x": `M18 6 6 18M6 6l12 12`,
  "alert": `M12 9v4M12 17h.01 M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z`,
  "info": `M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 16v-4M12 8h.01`,
  "check-circle": `M22 11.08V12a10 10 0 1 1-5.93-9.14 M22 4 12 14.01l-3-3`,
  "x-circle": `M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM15 9l-6 6M9 9l6 6`,
  "clock": `M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 6v6l4 2`,
  "download": `M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4 M7 10l5 5 5-5 M12 15V3`,
  "upload": `M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4 M17 8l-5-5-5 5 M12 3v12`,
  "trash": `M3 6h18 M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6 M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2 M10 11v6M14 11v6`,
  "stop": `M6 6h12v12H6z`,
  "rotate": `M3 12a9 9 0 1 0 2.64-6.36 M3 3v6h6`,
  "copy": `M20 9H11a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2v-9a2 2 0 0 0-2-2z M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1`,
  "chevron-right": `m9 18 6-6-6-6`,
  "chevron-down": `m6 9 6 6 6-6`,
  "arrow-left": `M19 12H5 M12 19l-7-7 7-7`,
  "arrow-right": `M5 12h14 M12 5l7 7-7 7`,
  "terminal": `m4 17 6-6-6-6 M12 19h8`,
  "database": `M12 2c5 0 9 1.34 9 3s-4 3-9 3-9-1.34-9-3 4-3 9-3z M21 12c0 1.66-4 3-9 3s-9-1.34-9-3 M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5`,
  "cpu": `M4 4h16v16H4z M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3 M9 9h6v6H9z`,
  "folder": `M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z`,
  "image": `M3 3h18v18H3z M8.5 10a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3z M21 15l-5-5L5 21`,
  "zap": `M13 2 3 14h9l-1 8 10-12h-9l1-8z`,
  "eye": `M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z`,
  "link": `M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71 M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71`,
  "loader": `M21 12a9 9 0 1 1-6.22-8.56`,
  "hard-drive": `M22 12H2 M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z M6 16h.01M10 16h.01`,
  "file": `M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z M14 2v6h6`,
  "sliders": `M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6`,
};

export const iconNames = Object.keys(PATHS);

export default {
  name: "Icon",
  props: { name: { type: String, required: true } },
  computed: {
    path() { return PATHS[this.name] || PATHS["info"]; },
    html() { return `<path d="${this.path}"/>`; },
  },
  template: `
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
         stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" v-html="html"></svg>
  `,
};
