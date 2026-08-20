import { useState } from 'react';

import { ArrowRight, FolderOpen } from 'lucide-react';
import { useTranslations } from 'use-intl';

import { isViewerSourceEnabled } from '../../../sources/source-config';
import { configurableViewerSources, type MapLookup } from '../../../sources/source-types';
import type { ViewerSource } from '../viewer-types';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { InputGroup, InputGroupButton, InputGroupInput } from '@/components/ui/input-group';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';

import logoUrl from '@/app/assets/logo.svg?url';

interface SourcePickerProps {
  choices: MapLookup[];
  input: string;
  visible: boolean;
  onChoose: (choice: MapLookup) => void;
  onAboutClick: () => void;
  onInputChange: (input: string) => void;
  onOpenFiles: () => void;
  onSubmit: (source: ViewerSource) => void;
}

const initialSource = configurableViewerSources.find(isViewerSourceEnabled) ?? 'beatsaver';

export function SourcePicker({
  choices,
  input,
  visible,
  onChoose,
  onAboutClick,
  onInputChange,
  onOpenFiles,
  onSubmit,
}: SourcePickerProps) {
  const t = useTranslations('source');
  const [source, setSource] = useState<ViewerSource>(initialSource);
  const scoreSaber = source === 'scoresaber';
  const beatLeader = source === 'beatleader';
  const isScore = scoreSaber || beatLeader;
  const trimmedInput = input.trim();
  const validInput = trimmedInput !== '' && (!isScore || /^\d+$/.test(trimmedInput));
  const inputLabel = scoreSaber
    ? t('scoresaberInputLabel')
    : beatLeader
      ? t('beatleaderInputLabel')
      : t('beatsaverInputLabel');
  const inputPlaceholder = scoreSaber
    ? t('scoresaberInputPlaceholder')
    : beatLeader
      ? t('beatleaderInputPlaceholder')
      : t('beatsaverInputPlaceholder');

  return (
    <>
      {visible && (
        <Card
          className="bg-card/65 fixed top-1/2 left-1/2 z-20 w-[min(34rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 overflow-hidden border-white/15 p-5 shadow-2xl shadow-black/35 backdrop-blur-2xl"
          role="group"
          aria-label={t('loadGroup')}
        >
          <div className="pointer-events-none absolute inset-x-16 top-0 h-px bg-gradient-to-r from-transparent via-white/60 to-transparent" />
          <h1 className="font-pixel mb-4 flex flex-col items-center justify-center gap-1 text-center text-xl font-medium tracking-widest [-webkit-text-stroke:0.8px_currentColor]">
            <img className="size-20 drop-shadow-[0_2px_3px_rgba(0,0,0,0.3)]" src={logoUrl} alt="" aria-hidden="true" />
            ChroViewer
          </h1>
          <ToggleGroup
            className="bg-muted/60 mb-3 grid auto-cols-fr grid-flow-col rounded-lg border p-1"
            type="single"
            value={source}
            aria-label={t('sourceType')}
            onValueChange={(value) => {
              if (value === 'beatsaver' || value === 'scoresaber' || value === 'beatleader') setSource(value);
            }}
          >
            {isViewerSourceEnabled('beatsaver') && (
              <ToggleGroupItem
                className="data-[state=on]:bg-background data-[state=on]:text-foreground data-[state=on]:hover:bg-background h-9 gap-1.5 px-2 text-sm hover:bg-white/10 data-[state=on]:shadow-sm sm:gap-2"
                value="beatsaver"
                aria-label={t('beatsaver')}
              >
                <img
                  className="size-4 sm:size-5"
                  src={`${import.meta.env.BASE_URL}beatsaver.svg`}
                  alt=""
                  aria-hidden="true"
                />
                <span className="hidden sm:inline">{t('beatsaver')}</span>
              </ToggleGroupItem>
            )}
            {isViewerSourceEnabled('scoresaber') && (
              <ToggleGroupItem
                className="data-[state=on]:bg-background data-[state=on]:text-foreground data-[state=on]:hover:bg-background h-9 gap-1.5 px-2 text-sm hover:bg-white/10 data-[state=on]:shadow-sm sm:gap-2"
                value="scoresaber"
                aria-label={t('scoresaber')}
              >
                <img
                  className="size-4 sm:size-5"
                  src={`${import.meta.env.BASE_URL}scoresaber.svg`}
                  alt=""
                  aria-hidden="true"
                />
                <span className="hidden sm:inline">{t('scoresaber')}</span>
              </ToggleGroupItem>
            )}
            {isViewerSourceEnabled('beatleader') && (
              <ToggleGroupItem
                className="data-[state=on]:bg-background data-[state=on]:text-foreground data-[state=on]:hover:bg-background h-9 gap-1.5 px-2 text-sm hover:bg-white/10 data-[state=on]:shadow-sm sm:gap-2"
                value="beatleader"
                aria-label={t('beatleader')}
              >
                <img
                  className="size-4 sm:size-5"
                  src={`${import.meta.env.BASE_URL}beatleader.svg`}
                  alt=""
                  aria-hidden="true"
                />
                <span className="hidden sm:inline">{t('beatleader')}</span>
              </ToggleGroupItem>
            )}
          </ToggleGroup>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              onSubmit(source);
            }}
          >
            <InputGroup>
              <InputGroupInput
                type="text"
                inputMode={isScore ? 'numeric' : 'text'}
                pattern={isScore ? '[0-9]*' : undefined}
                value={input}
                aria-label={inputLabel}
                placeholder={inputPlaceholder}
                onChange={(event) => {
                  onInputChange(event.currentTarget.value);
                }}
              />
              <InputGroupButton aria-label={t('openFiles')} title={t('openFiles')} onClick={onOpenFiles}>
                <FolderOpen />
              </InputGroupButton>
              <InputGroupButton
                type="submit"
                aria-label={isScore ? t('loadReplay') : t('loadMap')}
                disabled={!validInput}
              >
                <ArrowRight />
              </InputGroupButton>
            </InputGroup>
          </form>
          {scoreSaber && choices.length > 0 && (
            <section>
              <h2 className="text-muted-foreground mt-4 mb-2 text-xs font-medium">{t('multipleMatches')}</h2>
              <ul className="grid gap-1">
                {choices.map((choice) => (
                  <li key={choice.hash}>
                    <Button
                      type="button"
                      className="h-auto w-full justify-start text-left whitespace-normal"
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        onChoose(choice);
                      }}
                    >
                      {choice.label}
                    </Button>
                  </li>
                ))}
              </ul>
            </section>
          )}
          <button
            className="text-muted-foreground decoration-border hover:text-foreground hover:decoration-muted-foreground focus-visible:ring-ring/40 mx-auto mt-4 block rounded-sm text-[11px] underline underline-offset-4 transition-colors outline-none focus-visible:ring-3"
            type="button"
            aria-haspopup="dialog"
            onClick={onAboutClick}
          >
            {t('about')}
          </button>
        </Card>
      )}
    </>
  );
}
