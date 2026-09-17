# 2.2.4 release visual acceptance: light/dark 100%

Result: **PASS — 200 / 200 unique UI PNGs reviewed**. No unresolved visual issues in these two sets.

All 34 contact sheets were opened and every frame inspected. Suspect originals were opened separately. After the disclosure-icon pixmap fix, contact sheets were regenerated and all 11 replaced EventEditor frames per theme were reinspected; clean chevrons are visible in both themes. The individual final PNG hashes are recorded in main.json.

| Set | Unique UI PNGs reviewed | Report capture entries | Unique report names | Report passed | Horizontal overflow | Icon replacements reinspected |
| --- | ---: | ---: | ---: | --- | --- | ---: |
| light-100 | 100 / 100 | 101 | 100 | true | All 0 | 11 |
| dark-100 | 100 / 100 | 101 | 100 | true | All 0 | 11 |

The duplicate notes-source capture name is counted once. image-fixture.png is input image data and excluded from the UI count; nested profile attachment copies are also excluded. Reported capture names and root UI PNG inventories match exactly in both sets.

Checked: page hierarchy and separation; 10/11/12-month labels and centered category dots; empty/search states; note formatting, source/protected views, attachment and recording/playback states; note/reminder bulk none/mixed/all selection; real partial restore feedback; read-only reminder details; ordinary/small editor states and validation; long reminder bubbles and scroll endpoints; settings/update/backup states; statistics; menus and confirmation dialogs.

No control clipping, overlap, inaccessible fixed dialog actions, or inconsistent selection/restore counts found. Normal content cropping at the edge of a scrollable viewport is expected; the paired top/bottom captures show the remaining content. QMessageBox buttons retain the current Qt English labels.

Resolved during review: the initial disclosure icon noise was reported immediately, fixed by the editor owner, and verified against final replacement screenshots. No runtime code was changed by this review.

## Per-file inspection record

### light-100

| UI PNG | Review | Evidence |
| --- | --- | --- |
| [anniversary.png](../j224-release-screens/light-100/anniversary.png) | pass | light-100-01.jpg; icon recapture reinspected |
| [delete-confirmation.png](../j224-release-screens/light-100/delete-confirmation.png) | pass | light-100-01.jpg |
| [error-dialog.png](../j224-release-screens/light-100/error-dialog.png) | pass | light-100-01.jpg |
| [event-advanced.png](../j224-release-screens/light-100/event-advanced.png) | pass | light-100-01.jpg; original opened; icon recapture reinspected |
| [event-bubble-long-bottom.png](../j224-release-screens/light-100/event-bubble-long-bottom.png) | pass | light-100-01.jpg |
| [event-bubble-long.png](../j224-release-screens/light-100/event-bubble-long.png) | pass | light-100-01.jpg |
| [event-bubble.png](../j224-release-screens/light-100/event-bubble.png) | pass | light-100-02.jpg |
| [event-leap-month.png](../j224-release-screens/light-100/event-leap-month.png) | pass | light-100-02.jpg; icon recapture reinspected |
| [event-ledger.png](../j224-release-screens/light-100/event-ledger.png) | pass | light-100-02.jpg |
| [event-save-error.png](../j224-release-screens/light-100/event-save-error.png) | pass | light-100-02.jpg; icon recapture reinspected |
| [event-validation-error.png](../j224-release-screens/light-100/event-validation-error.png) | pass | light-100-02.jpg; original opened; icon recapture reinspected |
| [event.png](../j224-release-screens/light-100/event.png) | pass | light-100-02.jpg; icon recapture reinspected |
| [heading-menu.png](../j224-release-screens/light-100/heading-menu.png) | pass | light-100-03.jpg |
| [health-bubble.png](../j224-release-screens/light-100/health-bubble.png) | pass | light-100-03.jpg |
| [health-times.png](../j224-release-screens/light-100/health-times.png) | pass | light-100-03.jpg |
| [insert-link.png](../j224-release-screens/light-100/insert-link.png) | pass | light-100-03.jpg |
| [library-selector.png](../j224-release-screens/light-100/library-selector.png) | pass | light-100-03.jpg |
| [month-10.png](../j224-release-screens/light-100/month-10.png) | pass | light-100-03.jpg |
| [month-11.png](../j224-release-screens/light-100/month-11.png) | pass | light-100-04.jpg |
| [month-12.png](../j224-release-screens/light-100/month-12.png) | pass | light-100-04.jpg |
| [note-add-menu.png](../j224-release-screens/light-100/note-add-menu.png) | pass | light-100-04.jpg |
| [notes-attachments.png](../j224-release-screens/light-100/notes-attachments.png) | pass | light-100-04.jpg |
| [notes-bulk-confirm.png](../j224-release-screens/light-100/notes-bulk-confirm.png) | pass | light-100-04.jpg |
| [notes-image.png](../j224-release-screens/light-100/notes-image.png) | pass | light-100-04.jpg |
| [notes-protected.png](../j224-release-screens/light-100/notes-protected.png) | pass | light-100-05.jpg |
| [notes-search-empty.png](../j224-release-screens/light-100/notes-search-empty.png) | pass | light-100-05.jpg |
| [notes-source.png](../j224-release-screens/light-100/notes-source.png) | pass | light-100-05.jpg |
| [notes-trash-all.png](../j224-release-screens/light-100/notes-trash-all.png) | pass | light-100-05.jpg |
| [notes-trash-multiple.png](../j224-release-screens/light-100/notes-trash-multiple.png) | pass | light-100-05.jpg |
| [notes-trash-none.png](../j224-release-screens/light-100/notes-trash-none.png) | pass | light-100-05.jpg |
| [notes-trash.png](../j224-release-screens/light-100/notes-trash.png) | pass | light-100-06.jpg |
| [notes.png](../j224-release-screens/light-100/notes.png) | pass | light-100-06.jpg |
| [overview-empty.png](../j224-release-screens/light-100/overview-empty.png) | pass | light-100-06.jpg |
| [overview-note-filter.png](../j224-release-screens/light-100/overview-note-filter.png) | pass | light-100-06.jpg |
| [overview.png](../j224-release-screens/light-100/overview.png) | pass | light-100-06.jpg |
| [playback-active.png](../j224-release-screens/light-100/playback-active.png) | pass | light-100-06.jpg |
| [playback-completed.png](../j224-release-screens/light-100/playback-completed.png) | pass | light-100-07.jpg |
| [playback-dismissed.png](../j224-release-screens/light-100/playback-dismissed.png) | pass | light-100-07.jpg |
| [recording-active-simulated.png](../j224-release-screens/light-100/recording-active-simulated.png) | pass | light-100-07.jpg |
| [recording-collapsed-simulated.png](../j224-release-screens/light-100/recording-collapsed-simulated.png) | pass | light-100-07.jpg |
| [recording-error-simulated.png](../j224-release-screens/light-100/recording-error-simulated.png) | pass | light-100-07.jpg |
| [recording-idle.png](../j224-release-screens/light-100/recording-idle.png) | pass | light-100-07.jpg |
| [recording-paused-simulated.png](../j224-release-screens/light-100/recording-paused-simulated.png) | pass | light-100-08.jpg |
| [reminder-readonly.png](../j224-release-screens/light-100/reminder-readonly.png) | pass | light-100-08.jpg |
| [reminders-active.png](../j224-release-screens/light-100/reminders-active.png) | pass | light-100-08.jpg |
| [reminders-bulk-confirm.png](../j224-release-screens/light-100/reminders-bulk-confirm.png) | pass | light-100-08.jpg |
| [reminders-completed.png](../j224-release-screens/light-100/reminders-completed.png) | pass | light-100-08.jpg |
| [reminders-pending.png](../j224-release-screens/light-100/reminders-pending.png) | pass | light-100-08.jpg |
| [reminders-search-empty.png](../j224-release-screens/light-100/reminders-search-empty.png) | pass | light-100-09.jpg |
| [reminders-trash-all.png](../j224-release-screens/light-100/reminders-trash-all.png) | pass | light-100-09.jpg |
| [reminders-trash-none.png](../j224-release-screens/light-100/reminders-trash-none.png) | pass | light-100-09.jpg |
| [reminders-trash-pending.png](../j224-release-screens/light-100/reminders-trash-pending.png) | pass | light-100-09.jpg |
| [reminders-trash.png](../j224-release-screens/light-100/reminders-trash.png) | pass | light-100-09.jpg |
| [reminders.png](../j224-release-screens/light-100/reminders.png) | pass | light-100-09.jpg |
| [settings-backup-feedback.png](../j224-release-screens/light-100/settings-backup-feedback.png) | pass | light-100-10.jpg |
| [settings-update-available.png](../j224-release-screens/light-100/settings-update-available.png) | pass | light-100-10.jpg |
| [settings-update-error.png](../j224-release-screens/light-100/settings-update-error.png) | pass | light-100-10.jpg |
| [settings-update-latest.png](../j224-release-screens/light-100/settings-update-latest.png) | pass | light-100-10.jpg |
| [settings.png](../j224-release-screens/light-100/settings.png) | pass | light-100-10.jpg |
| [small-anniversary-bottom.png](../j224-release-screens/light-100/small-anniversary-bottom.png) | pass | light-100-10.jpg; icon recapture reinspected |
| [small-anniversary.png](../j224-release-screens/light-100/small-anniversary.png) | pass | light-100-11.jpg; icon recapture reinspected |
| [small-event-bottom.png](../j224-release-screens/light-100/small-event-bottom.png) | pass | light-100-11.jpg; icon recapture reinspected |
| [small-event-save-error.png](../j224-release-screens/light-100/small-event-save-error.png) | pass | light-100-11.jpg; icon recapture reinspected |
| [small-event.png](../j224-release-screens/light-100/small-event.png) | pass | light-100-11.jpg; icon recapture reinspected |
| [small-month-10.png](../j224-release-screens/light-100/small-month-10.png) | pass | light-100-11.jpg |
| [small-month-11.png](../j224-release-screens/light-100/small-month-11.png) | pass | light-100-11.jpg |
| [small-month-12.png](../j224-release-screens/light-100/small-month-12.png) | pass | light-100-12.jpg |
| [small-notes-bottom.png](../j224-release-screens/light-100/small-notes-bottom.png) | pass | light-100-12.jpg |
| [small-notes-bulk-confirm.png](../j224-release-screens/light-100/small-notes-bulk-confirm.png) | pass | light-100-12.jpg |
| [small-notes-source-bottom.png](../j224-release-screens/light-100/small-notes-source-bottom.png) | pass | light-100-12.jpg |
| [small-notes-source.png](../j224-release-screens/light-100/small-notes-source.png) | pass | light-100-12.jpg |
| [small-notes-toolbar-bottom.png](../j224-release-screens/light-100/small-notes-toolbar-bottom.png) | pass | light-100-12.jpg |
| [small-notes-trash-all.png](../j224-release-screens/light-100/small-notes-trash-all.png) | pass | light-100-13.jpg |
| [small-notes-trash-multiple.png](../j224-release-screens/light-100/small-notes-trash-multiple.png) | pass | light-100-13.jpg |
| [small-notes-trash-none.png](../j224-release-screens/light-100/small-notes-trash-none.png) | pass | light-100-13.jpg |
| [small-notes-trash-preview-bottom.png](../j224-release-screens/light-100/small-notes-trash-preview-bottom.png) | pass | light-100-13.jpg |
| [small-notes.png](../j224-release-screens/light-100/small-notes.png) | pass | light-100-13.jpg |
| [small-overview-bottom.png](../j224-release-screens/light-100/small-overview-bottom.png) | pass | light-100-13.jpg |
| [small-overview.png](../j224-release-screens/light-100/small-overview.png) | pass | light-100-14.jpg |
| [small-recording-bottom.png](../j224-release-screens/light-100/small-recording-bottom.png) | pass | light-100-14.jpg |
| [small-recording.png](../j224-release-screens/light-100/small-recording.png) | pass | light-100-14.jpg |
| [small-reminder-readonly.png](../j224-release-screens/light-100/small-reminder-readonly.png) | pass | light-100-14.jpg |
| [small-reminders-bottom.png](../j224-release-screens/light-100/small-reminders-bottom.png) | pass | light-100-14.jpg |
| [small-reminders-bulk-confirm.png](../j224-release-screens/light-100/small-reminders-bulk-confirm.png) | pass | light-100-14.jpg |
| [small-reminders-pending.png](../j224-release-screens/light-100/small-reminders-pending.png) | pass | light-100-15.jpg |
| [small-reminders-trash-all.png](../j224-release-screens/light-100/small-reminders-trash-all.png) | pass | light-100-15.jpg |
| [small-reminders-trash-none.png](../j224-release-screens/light-100/small-reminders-trash-none.png) | pass | light-100-15.jpg |
| [small-reminders-trash-pending.png](../j224-release-screens/light-100/small-reminders-trash-pending.png) | pass | light-100-15.jpg |
| [small-reminders.png](../j224-release-screens/light-100/small-reminders.png) | pass | light-100-15.jpg |
| [small-settings-bottom.png](../j224-release-screens/light-100/small-settings-bottom.png) | pass | light-100-15.jpg |
| [small-settings.png](../j224-release-screens/light-100/small-settings.png) | pass | light-100-16.jpg |
| [small-statistics-bottom.png](../j224-release-screens/light-100/small-statistics-bottom.png) | pass | light-100-16.jpg |
| [small-statistics.png](../j224-release-screens/light-100/small-statistics.png) | pass | light-100-16.jpg |
| [snooze.png](../j224-release-screens/light-100/snooze.png) | pass | light-100-16.jpg |
| [statistics-day.png](../j224-release-screens/light-100/statistics-day.png) | pass | light-100-16.jpg |
| [statistics-empty.png](../j224-release-screens/light-100/statistics-empty.png) | pass | light-100-16.jpg |
| [statistics-month.png](../j224-release-screens/light-100/statistics-month.png) | pass | light-100-17.jpg |
| [statistics-week.png](../j224-release-screens/light-100/statistics-week.png) | pass | light-100-17.jpg |
| [statistics-year.png](../j224-release-screens/light-100/statistics-year.png) | pass | light-100-17.jpg |
| [statistics.png](../j224-release-screens/light-100/statistics.png) | pass | light-100-17.jpg |

### dark-100

| UI PNG | Review | Evidence |
| --- | --- | --- |
| [anniversary.png](../j224-release-screens/dark-100/anniversary.png) | pass | dark-100-01.jpg; icon recapture reinspected |
| [delete-confirmation.png](../j224-release-screens/dark-100/delete-confirmation.png) | pass | dark-100-01.jpg |
| [error-dialog.png](../j224-release-screens/dark-100/error-dialog.png) | pass | dark-100-01.jpg |
| [event-advanced.png](../j224-release-screens/dark-100/event-advanced.png) | pass | dark-100-01.jpg; original opened; icon recapture reinspected |
| [event-bubble-long-bottom.png](../j224-release-screens/dark-100/event-bubble-long-bottom.png) | pass | dark-100-01.jpg |
| [event-bubble-long.png](../j224-release-screens/dark-100/event-bubble-long.png) | pass | dark-100-01.jpg |
| [event-bubble.png](../j224-release-screens/dark-100/event-bubble.png) | pass | dark-100-02.jpg |
| [event-leap-month.png](../j224-release-screens/dark-100/event-leap-month.png) | pass | dark-100-02.jpg; icon recapture reinspected |
| [event-ledger.png](../j224-release-screens/dark-100/event-ledger.png) | pass | dark-100-02.jpg |
| [event-save-error.png](../j224-release-screens/dark-100/event-save-error.png) | pass | dark-100-02.jpg; icon recapture reinspected |
| [event-validation-error.png](../j224-release-screens/dark-100/event-validation-error.png) | pass | dark-100-02.jpg; icon recapture reinspected |
| [event.png](../j224-release-screens/dark-100/event.png) | pass | dark-100-02.jpg; icon recapture reinspected |
| [heading-menu.png](../j224-release-screens/dark-100/heading-menu.png) | pass | dark-100-03.jpg |
| [health-bubble.png](../j224-release-screens/dark-100/health-bubble.png) | pass | dark-100-03.jpg |
| [health-times.png](../j224-release-screens/dark-100/health-times.png) | pass | dark-100-03.jpg |
| [insert-link.png](../j224-release-screens/dark-100/insert-link.png) | pass | dark-100-03.jpg |
| [library-selector.png](../j224-release-screens/dark-100/library-selector.png) | pass | dark-100-03.jpg |
| [month-10.png](../j224-release-screens/dark-100/month-10.png) | pass | dark-100-03.jpg |
| [month-11.png](../j224-release-screens/dark-100/month-11.png) | pass | dark-100-04.jpg |
| [month-12.png](../j224-release-screens/dark-100/month-12.png) | pass | dark-100-04.jpg |
| [note-add-menu.png](../j224-release-screens/dark-100/note-add-menu.png) | pass | dark-100-04.jpg |
| [notes-attachments.png](../j224-release-screens/dark-100/notes-attachments.png) | pass | dark-100-04.jpg |
| [notes-bulk-confirm.png](../j224-release-screens/dark-100/notes-bulk-confirm.png) | pass | dark-100-04.jpg |
| [notes-image.png](../j224-release-screens/dark-100/notes-image.png) | pass | dark-100-04.jpg |
| [notes-protected.png](../j224-release-screens/dark-100/notes-protected.png) | pass | dark-100-05.jpg |
| [notes-search-empty.png](../j224-release-screens/dark-100/notes-search-empty.png) | pass | dark-100-05.jpg |
| [notes-source.png](../j224-release-screens/dark-100/notes-source.png) | pass | dark-100-05.jpg |
| [notes-trash-all.png](../j224-release-screens/dark-100/notes-trash-all.png) | pass | dark-100-05.jpg |
| [notes-trash-multiple.png](../j224-release-screens/dark-100/notes-trash-multiple.png) | pass | dark-100-05.jpg |
| [notes-trash-none.png](../j224-release-screens/dark-100/notes-trash-none.png) | pass | dark-100-05.jpg |
| [notes-trash.png](../j224-release-screens/dark-100/notes-trash.png) | pass | dark-100-06.jpg |
| [notes.png](../j224-release-screens/dark-100/notes.png) | pass | dark-100-06.jpg |
| [overview-empty.png](../j224-release-screens/dark-100/overview-empty.png) | pass | dark-100-06.jpg |
| [overview-note-filter.png](../j224-release-screens/dark-100/overview-note-filter.png) | pass | dark-100-06.jpg |
| [overview.png](../j224-release-screens/dark-100/overview.png) | pass | dark-100-06.jpg |
| [playback-active.png](../j224-release-screens/dark-100/playback-active.png) | pass | dark-100-06.jpg |
| [playback-completed.png](../j224-release-screens/dark-100/playback-completed.png) | pass | dark-100-07.jpg |
| [playback-dismissed.png](../j224-release-screens/dark-100/playback-dismissed.png) | pass | dark-100-07.jpg |
| [recording-active-simulated.png](../j224-release-screens/dark-100/recording-active-simulated.png) | pass | dark-100-07.jpg |
| [recording-collapsed-simulated.png](../j224-release-screens/dark-100/recording-collapsed-simulated.png) | pass | dark-100-07.jpg |
| [recording-error-simulated.png](../j224-release-screens/dark-100/recording-error-simulated.png) | pass | dark-100-07.jpg |
| [recording-idle.png](../j224-release-screens/dark-100/recording-idle.png) | pass | dark-100-07.jpg |
| [recording-paused-simulated.png](../j224-release-screens/dark-100/recording-paused-simulated.png) | pass | dark-100-08.jpg |
| [reminder-readonly.png](../j224-release-screens/dark-100/reminder-readonly.png) | pass | dark-100-08.jpg |
| [reminders-active.png](../j224-release-screens/dark-100/reminders-active.png) | pass | dark-100-08.jpg |
| [reminders-bulk-confirm.png](../j224-release-screens/dark-100/reminders-bulk-confirm.png) | pass | dark-100-08.jpg |
| [reminders-completed.png](../j224-release-screens/dark-100/reminders-completed.png) | pass | dark-100-08.jpg |
| [reminders-pending.png](../j224-release-screens/dark-100/reminders-pending.png) | pass | dark-100-08.jpg |
| [reminders-search-empty.png](../j224-release-screens/dark-100/reminders-search-empty.png) | pass | dark-100-09.jpg |
| [reminders-trash-all.png](../j224-release-screens/dark-100/reminders-trash-all.png) | pass | dark-100-09.jpg |
| [reminders-trash-none.png](../j224-release-screens/dark-100/reminders-trash-none.png) | pass | dark-100-09.jpg |
| [reminders-trash-pending.png](../j224-release-screens/dark-100/reminders-trash-pending.png) | pass | dark-100-09.jpg |
| [reminders-trash.png](../j224-release-screens/dark-100/reminders-trash.png) | pass | dark-100-09.jpg |
| [reminders.png](../j224-release-screens/dark-100/reminders.png) | pass | dark-100-09.jpg |
| [settings-backup-feedback.png](../j224-release-screens/dark-100/settings-backup-feedback.png) | pass | dark-100-10.jpg |
| [settings-update-available.png](../j224-release-screens/dark-100/settings-update-available.png) | pass | dark-100-10.jpg |
| [settings-update-error.png](../j224-release-screens/dark-100/settings-update-error.png) | pass | dark-100-10.jpg |
| [settings-update-latest.png](../j224-release-screens/dark-100/settings-update-latest.png) | pass | dark-100-10.jpg |
| [settings.png](../j224-release-screens/dark-100/settings.png) | pass | dark-100-10.jpg |
| [small-anniversary-bottom.png](../j224-release-screens/dark-100/small-anniversary-bottom.png) | pass | dark-100-10.jpg; icon recapture reinspected |
| [small-anniversary.png](../j224-release-screens/dark-100/small-anniversary.png) | pass | dark-100-11.jpg; icon recapture reinspected |
| [small-event-bottom.png](../j224-release-screens/dark-100/small-event-bottom.png) | pass | dark-100-11.jpg; icon recapture reinspected |
| [small-event-save-error.png](../j224-release-screens/dark-100/small-event-save-error.png) | pass | dark-100-11.jpg; icon recapture reinspected |
| [small-event.png](../j224-release-screens/dark-100/small-event.png) | pass | dark-100-11.jpg; icon recapture reinspected |
| [small-month-10.png](../j224-release-screens/dark-100/small-month-10.png) | pass | dark-100-11.jpg |
| [small-month-11.png](../j224-release-screens/dark-100/small-month-11.png) | pass | dark-100-11.jpg |
| [small-month-12.png](../j224-release-screens/dark-100/small-month-12.png) | pass | dark-100-12.jpg |
| [small-notes-bottom.png](../j224-release-screens/dark-100/small-notes-bottom.png) | pass | dark-100-12.jpg; original opened |
| [small-notes-bulk-confirm.png](../j224-release-screens/dark-100/small-notes-bulk-confirm.png) | pass | dark-100-12.jpg |
| [small-notes-source-bottom.png](../j224-release-screens/dark-100/small-notes-source-bottom.png) | pass | dark-100-12.jpg |
| [small-notes-source.png](../j224-release-screens/dark-100/small-notes-source.png) | pass | dark-100-12.jpg |
| [small-notes-toolbar-bottom.png](../j224-release-screens/dark-100/small-notes-toolbar-bottom.png) | pass | dark-100-12.jpg |
| [small-notes-trash-all.png](../j224-release-screens/dark-100/small-notes-trash-all.png) | pass | dark-100-13.jpg |
| [small-notes-trash-multiple.png](../j224-release-screens/dark-100/small-notes-trash-multiple.png) | pass | dark-100-13.jpg |
| [small-notes-trash-none.png](../j224-release-screens/dark-100/small-notes-trash-none.png) | pass | dark-100-13.jpg |
| [small-notes-trash-preview-bottom.png](../j224-release-screens/dark-100/small-notes-trash-preview-bottom.png) | pass | dark-100-13.jpg |
| [small-notes.png](../j224-release-screens/dark-100/small-notes.png) | pass | dark-100-13.jpg |
| [small-overview-bottom.png](../j224-release-screens/dark-100/small-overview-bottom.png) | pass | dark-100-13.jpg |
| [small-overview.png](../j224-release-screens/dark-100/small-overview.png) | pass | dark-100-14.jpg |
| [small-recording-bottom.png](../j224-release-screens/dark-100/small-recording-bottom.png) | pass | dark-100-14.jpg |
| [small-recording.png](../j224-release-screens/dark-100/small-recording.png) | pass | dark-100-14.jpg |
| [small-reminder-readonly.png](../j224-release-screens/dark-100/small-reminder-readonly.png) | pass | dark-100-14.jpg |
| [small-reminders-bottom.png](../j224-release-screens/dark-100/small-reminders-bottom.png) | pass | dark-100-14.jpg |
| [small-reminders-bulk-confirm.png](../j224-release-screens/dark-100/small-reminders-bulk-confirm.png) | pass | dark-100-14.jpg |
| [small-reminders-pending.png](../j224-release-screens/dark-100/small-reminders-pending.png) | pass | dark-100-15.jpg |
| [small-reminders-trash-all.png](../j224-release-screens/dark-100/small-reminders-trash-all.png) | pass | dark-100-15.jpg |
| [small-reminders-trash-none.png](../j224-release-screens/dark-100/small-reminders-trash-none.png) | pass | dark-100-15.jpg |
| [small-reminders-trash-pending.png](../j224-release-screens/dark-100/small-reminders-trash-pending.png) | pass | dark-100-15.jpg |
| [small-reminders.png](../j224-release-screens/dark-100/small-reminders.png) | pass | dark-100-15.jpg |
| [small-settings-bottom.png](../j224-release-screens/dark-100/small-settings-bottom.png) | pass | dark-100-15.jpg |
| [small-settings.png](../j224-release-screens/dark-100/small-settings.png) | pass | dark-100-16.jpg |
| [small-statistics-bottom.png](../j224-release-screens/dark-100/small-statistics-bottom.png) | pass | dark-100-16.jpg |
| [small-statistics.png](../j224-release-screens/dark-100/small-statistics.png) | pass | dark-100-16.jpg |
| [snooze.png](../j224-release-screens/dark-100/snooze.png) | pass | dark-100-16.jpg |
| [statistics-day.png](../j224-release-screens/dark-100/statistics-day.png) | pass | dark-100-16.jpg |
| [statistics-empty.png](../j224-release-screens/dark-100/statistics-empty.png) | pass | dark-100-16.jpg |
| [statistics-month.png](../j224-release-screens/dark-100/statistics-month.png) | pass | dark-100-17.jpg |
| [statistics-week.png](../j224-release-screens/dark-100/statistics-week.png) | pass | dark-100-17.jpg |
| [statistics-year.png](../j224-release-screens/dark-100/statistics-year.png) | pass | dark-100-17.jpg |
| [statistics.png](../j224-release-screens/dark-100/statistics.png) | pass | dark-100-17.jpg |
