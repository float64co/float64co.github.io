// What each folder in public/music is called on air. A folder not listed here
// goes by its own name. Change these freely.
export const STATION_NAMES = {
  dnb: 'Amen FM',
  dubstep: 'Wobble FM',
  '': 'Block Radio', // tracks not in any folder
};
export const stationName = (folder) => STATION_NAMES[folder] ?? folder;
