import { type MouseEvent, useState } from 'react';

import { CircleHelp, ExternalLink, Heart, X } from 'lucide-react';
import { Dialog } from 'radix-ui';
import { useTranslations } from 'use-intl';

import { Avatar, AvatarFallback, AvatarGroup, AvatarImage } from '@/components/ui/avatar';
import { Button } from '@/components/ui/button';
import { Kbd } from '@/components/ui/kbd';
import { Separator } from '@/components/ui/separator';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';

import blissAvatarUrl from '@/app/assets/contributors/bliss.jpg?url';
import caedenAvatarUrl from '@/app/assets/contributors/caeden117.png?url';
import lexediaAvatarUrl from '@/app/assets/contributors/lexedia.png?url';
import lillieAvatarUrl from '@/app/assets/contributors/lillie.png?url';
import mawnteeAvatarUrl from '@/app/assets/contributors/mawntee.jpg?url';
import reaxtAvatarUrl from '@/app/assets/contributors/reaxt.png?url';
import swifterAvatarUrl from '@/app/assets/contributors/swifter1243.png?url';
import umbranoxAvatarUrl from '@/app/assets/contributors/umbranox.png?url';
import logoUrl from '@/app/assets/logo.svg?url';

interface AboutDialogProps {
  open: boolean;
  showTrigger: boolean;
  onOpenChange: (open: boolean) => void;
}

const contributors = [
  { name: 'Caeden117', href: 'https://github.com/Caeden117', avatarUrl: caedenAvatarUrl, fallback: 'C' },
  { name: 'Swifter', href: 'https://github.com/Swifter1243', avatarUrl: swifterAvatarUrl, fallback: 'S' },
  { name: 'Reaxt', href: 'https://github.com/Reaxt', avatarUrl: reaxtAvatarUrl, fallback: 'R' },
  { name: 'Mawntee', href: 'https://github.com/mawntee', avatarUrl: mawnteeAvatarUrl, fallback: 'M' },
  { name: 'Lexedia', href: 'https://github.com/Lexedia', avatarUrl: lexediaAvatarUrl, fallback: 'L' },
  { name: 'Bliss', href: 'https://github.com/Bliss-tbh', avatarUrl: blissAvatarUrl, fallback: 'B' },
  { name: 'Lillie', href: 'https://github.com/iLillie', avatarUrl: lillieAvatarUrl, fallback: 'L' },
];

const externalLinkClass =
  'text-foreground decoration-border hover:decoration-muted-foreground inline-flex items-center gap-1 underline underline-offset-4 transition-colors';

export function AboutDialog({ open, showTrigger, onOpenChange }: AboutDialogProps) {
  const t = useTranslations('viewer.about');
  const tc = useTranslations('common');
  const [openContributor, setOpenContributor] = useState<string | null>(null);
  const [armedContributor, setArmedContributor] = useState<string | null>(null);
  const shortcuts = [
    [t('keys.space'), t('actions.playPause')],
    [t('keys.leftRight'), t('actions.seek')],
    ['F', t('actions.fullscreen')],
    ['H', t('actions.hideControls')],
    ['M', t('actions.toggleHitsounds')],
    ['?', t('actions.openAbout')],
  ];

  function handleContributorClick(event: MouseEvent<HTMLAnchorElement>, name: string) {
    if (window.matchMedia('(hover: hover)').matches || armedContributor === name) return;
    event.preventDefault();
    setArmedContributor(name);
    setOpenContributor(name);
  }

  function handleContributorOpenChange(name: string, nextOpen: boolean) {
    setOpenContributor((current) => (nextOpen ? name : current === name ? null : current));
    if (!nextOpen) setArmedContributor((current) => (current === name ? null : current));
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      {showTrigger && (
        <Dialog.Trigger asChild>
          <Button variant="ghost" size="icon-sm" aria-label={tc('about')} title={tc('about')} aria-expanded={open}>
            <CircleHelp />
          </Button>
        </Dialog.Trigger>
      )}
      <Dialog.Portal>
        <Dialog.Overlay className="data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 fixed inset-0 z-50 bg-black/70 backdrop-blur-sm" />
        <Dialog.Content className="data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 border-border bg-popover text-popover-foreground fixed top-1/2 left-1/2 z-50 flex max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-2xl -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-xl border shadow-2xl shadow-black/60 duration-200 outline-none">
          <div className="pointer-events-none absolute inset-x-16 top-0 h-px bg-gradient-to-r from-transparent via-white/60 to-transparent" />
          <header className="px-8 pt-8 pb-6 max-sm:px-5 max-sm:pt-7">
            <div className="flex items-center gap-4" aria-hidden="true">
              <div className="to-border h-px flex-1 bg-gradient-to-r from-transparent" />
              <div className="flex shrink-0 items-center gap-2.5">
                <img className="size-10 drop-shadow-[0_0_12px_rgba(255,255,255,0.14)]" src={logoUrl} alt="" />
                <span className="font-pixel text-xl font-medium tracking-[0.12em]">ChroViewer</span>
              </div>
              <div className="to-border h-px flex-1 bg-gradient-to-l from-transparent" />
            </div>
            <Dialog.Title className="sr-only">{t('title')}</Dialog.Title>
            <Dialog.Description className="text-muted-foreground mt-3 text-center text-xs tracking-wide">
              {t('tagline')}
            </Dialog.Description>
          </header>

          <div className="overflow-y-auto px-8 pb-7 max-sm:px-5">
            <section aria-labelledby="about-people-heading">
              <h2 id="about-people-heading" className="sr-only">
                {t('people')}
              </h2>

              <div className="flex items-center justify-between gap-5 px-1 max-sm:flex-col max-sm:items-stretch max-sm:gap-4">
                <a
                  className="focus-visible:ring-ring/40 flex min-w-0 items-center gap-3 rounded-md outline-none focus-visible:ring-3"
                  href="https://github.com/Umbranoxio"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <Avatar size="lg">
                    <AvatarImage src={umbranoxAvatarUrl} alt="" />
                    <AvatarFallback>U</AvatarFallback>
                  </Avatar>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-semibold">Umbranox</span>
                    <span className="text-muted-foreground block text-[11px]">{t('roles.leadDeveloper')}</span>
                  </span>
                  <ExternalLink className="text-muted-foreground size-3.5 shrink-0 max-sm:hidden" aria-hidden="true" />
                </a>

                <div className="flex shrink-0 items-center gap-3 max-sm:justify-between max-sm:gap-2">
                  <span className="text-muted-foreground text-[10px] font-medium tracking-widest uppercase">
                    {t('contributors')}
                  </span>
                  <TooltipProvider delayDuration={100}>
                    <AvatarGroup className="max-sm:-space-x-2.5">
                      {contributors.map((contributor) => (
                        <Tooltip
                          open={openContributor === contributor.name}
                          onOpenChange={(nextOpen) => {
                            handleContributorOpenChange(contributor.name, nextOpen);
                          }}
                          key={contributor.name}
                        >
                          <TooltipTrigger asChild>
                            <a
                              className="focus-visible:ring-ring/40 ring-background rounded-full ring-2 outline-none focus-visible:ring-3"
                              href={contributor.href}
                              target="_blank"
                              rel="noopener noreferrer"
                              aria-label={`${contributor.name}, ${t('roles.contributor')}`}
                              onClick={(event) => {
                                handleContributorClick(event, contributor.name);
                              }}
                            >
                              <Avatar className="max-sm:size-7" size="lg">
                                <AvatarImage src={contributor.avatarUrl} alt="" />
                                <AvatarFallback>{contributor.fallback}</AvatarFallback>
                              </Avatar>
                            </a>
                          </TooltipTrigger>
                          <TooltipContent side="top">{contributor.name}</TooltipContent>
                        </Tooltip>
                      ))}
                    </AvatarGroup>
                  </TooltipProvider>
                </div>
              </div>
            </section>

            <Separator className="my-5" />

            <section
              className="flex flex-wrap items-center gap-x-5 gap-y-3 px-1 max-sm:flex-col max-sm:items-stretch"
              aria-labelledby="about-support-heading"
            >
              <div className="flex items-center gap-2">
                <Heart className="text-muted-foreground size-3.5" aria-hidden="true" />
                <h2 id="about-support-heading" className="text-[10px] font-semibold tracking-widest uppercase">
                  {t('support.title')}
                </h2>
              </div>
              <div className="flex flex-1 flex-wrap gap-x-5 gap-y-2 max-sm:pl-5 sm:justify-end">
                <a
                  className="focus-visible:ring-ring/40 flex items-center gap-1.5 rounded-md text-xs font-medium outline-none focus-visible:ring-3"
                  href="https://www.patreon.com/scoresaber"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {t('support.chroViewer')}
                  <ExternalLink className="text-muted-foreground size-3" aria-hidden="true" />
                </a>
                <a
                  className="focus-visible:ring-ring/40 flex items-center gap-1.5 rounded-md text-xs font-medium outline-none focus-visible:ring-3"
                  href="https://www.patreon.com/Caeden117"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {t('support.chroMapper')}
                  <ExternalLink className="text-muted-foreground size-3" aria-hidden="true" />
                </a>
              </div>
            </section>

            <div className="hidden lg:block">
              <Separator className="my-5" />
              <section className="flex items-center gap-4 px-1" aria-labelledby="about-keyboard-heading">
                <h2
                  id="about-keyboard-heading"
                  className="text-muted-foreground shrink-0 text-[10px] font-medium tracking-widest uppercase"
                >
                  <span aria-hidden="true">{t('keysLabel')}</span>
                  <span className="sr-only">{t('keyboardShortcuts')}</span>
                </h2>
                <ul className="flex flex-1 items-center justify-between gap-2 text-[10px]">
                  {shortcuts.map(([key, action]) => (
                    <li className="flex items-center gap-1.5 whitespace-nowrap" key={key}>
                      <Kbd>{key}</Kbd>
                      <span className="text-muted-foreground">{action}</span>
                    </li>
                  ))}
                </ul>
              </section>
            </div>

            <Separator className="my-5" />

            <section
              className="text-muted-foreground grid items-center gap-x-5 gap-y-3 text-[11px] sm:grid-cols-[minmax(0,1fr)_auto]"
              aria-labelledby="about-attributions-heading"
            >
              <h2 id="about-attributions-heading" className="sr-only">
                {t('attributions.title')}
              </h2>
              <p className="leading-relaxed">
                {t.rich('attributions.chroMapper', {
                  chroMapper: (chunks) => (
                    <a
                      className={externalLinkClass}
                      href="https://github.com/Caeden117/ChroMapper"
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {chunks}
                    </a>
                  ),
                })}
              </p>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2 sm:justify-end">
                <span>{t('license')}</span>
                <a
                  className={externalLinkClass}
                  href="https://github.com/Umbranoxio/chroviewer/blob/main/ATTRIBUTIONS.md"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {t('attributions.viewAll')}
                </a>
                <a
                  className={externalLinkClass}
                  href="https://github.com/Umbranoxio/chroviewer"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <svg className="size-3.5" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                    <path d="M12 .3a12 12 0 0 0-3.8 23.4c.6.1.8-.3.8-.6v-2.3c-3.3.7-4-1.4-4-1.4-.5-1.4-1.3-1.8-1.3-1.8-1.1-.7.1-.7.1-.7 1.2.1 1.8 1.2 1.8 1.2 1.1 1.8 2.8 1.3 3.5 1 .1-.8.4-1.3.8-1.6-2.7-.3-5.5-1.3-5.5-5.9 0-1.3.5-2.4 1.2-3.2-.1-.3-.5-1.5.1-3.2 0 0 1-.3 3.3 1.2a11.5 11.5 0 0 1 6 0C16 4.7 17 5 17 5c.6 1.7.2 2.9.1 3.2.8.8 1.2 1.9 1.2 3.2 0 4.6-2.8 5.6-5.5 5.9.4.4.8 1.1.8 2.2v3.3c0 .3.2.7.8.6A12 12 0 0 0 12 .3Z" />
                  </svg>
                  {t('source')}
                </a>
              </div>
            </section>
          </div>

          <Dialog.Close className="text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-ring/40 absolute top-3 right-3 flex size-8 items-center justify-center rounded-md transition-colors outline-none focus-visible:ring-3">
            <X className="size-4" />
            <span className="sr-only">{tc('close')}</span>
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
