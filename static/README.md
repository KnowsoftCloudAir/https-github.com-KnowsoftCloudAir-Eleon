# Knowsoft Churchgate – PWA Files

Add these files to your existing project so it becomes installable and ready for Google Play Store.

## Folder Structure

```
your-app/
├── icons/
│   ├── icon-192.png
│   ├── icon-512.png
│   └── icon-512-maskable.png
├── manifest.json
└── sw.js
```

## How to Install These Files

1. Copy the entire `icons` folder into the **public** folder of your project  
   (or the root if you are using plain HTML / static site).

2. Copy `manifest.json` into the same public / root folder.

3. Copy `sw.js` into the same public / root folder.

4. Add these lines inside the `<head>` of your main HTML file (or layout):

```html
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#0B1220">
<link rel="apple-touch-icon" href="/icons/icon-192.png">
```

5. Register the service worker (add this script just before the closing `</body>` tag):

```html
<script>
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js')
        .then(reg => console.log('Service Worker registered', reg))
        .catch(err => console.log('Service Worker registration failed', err));
    });
  }
</script>
```

6. Push everything to GitHub. Render will automatically redeploy.

## After Deploy

1. Visit https://churchgate.onrender.com/manifest.json → should show the JSON
2. Go to https://www.pwabuilder.com and enter your URL to test
3. Then package for Android / Google Play

## Icon Notes

- icon-512.png → used for Play Store and home screen
- icon-192.png → used for smaller displays
- icon-512-maskable.png → safer version for Android adaptive icons
