import type { Locale } from './config';
import { intlFormats } from './formats';
import enMessages from './messages/en.json';

declare module 'use-intl' {
  interface AppConfig {
    Formats: typeof intlFormats;
    Locale: Locale;
    Messages: typeof enMessages;
  }
}
