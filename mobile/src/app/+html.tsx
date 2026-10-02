import { ScrollViewStyleReset } from 'expo-router/html';
import type { PropsWithChildren } from 'react';

/**
 * Customises the HTML document that wraps the web build.
 *
 * `viewport-fit=cover` is the important part: without it iOS Safari reports
 * every CSS `env(safe-area-inset-*)` as 0, so useSafeAreaInsets() returns
 * zeroes and the tab bar renders underneath the home indicator.
 *
 * This file has no effect on the native (iOS/Android) builds.
 */
export default function Root({ children }: PropsWithChildren) {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta httpEquiv="X-UA-Compatible" content="IE=edge" />
        <meta
          name="viewport"
          content="width=device-width, initial-scale=1, shrink-to-fit=no, viewport-fit=cover"
        />

        {/* Standalone look when added to the iOS home screen. */}
        <meta name="apple-mobile-web-app-capable" content="yes" />
        <meta name="apple-mobile-web-app-status-bar-style" content="default" />
        <meta name="apple-mobile-web-app-title" content="Taste of Ethiopia" />
        <meta name="theme-color" content="#FFFDF8" />

        <ScrollViewStyleReset />

        {/*
          iOS Safari draws its bottom toolbar *over* the page, so a 100vh app
          has its last ~50px (here: the tab bar labels) hidden behind it.
          100dvh is the dynamic viewport height, which excludes browser chrome,
          so the tab bar ends where the visible area ends. The plain 100% line
          is the fallback for browsers without dvh support.
        */}
        <style
          dangerouslySetInnerHTML={{
            __html: `
              html, body, #root { height: 100%; }
              @supports (height: 100dvh) {
                html, body, #root { height: 100dvh; }
              }
            `,
          }}
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
