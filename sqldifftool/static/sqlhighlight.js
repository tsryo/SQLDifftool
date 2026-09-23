/* Minimal T-SQL highlighter. Returns, per line, a list of [start, end, className] runs.
   Block comments and strings that span lines are tracked across lines. */
(function (global) {
  'use strict';

  const KEYWORDS = new Set((
    'add after all alter and any as asc authorization begin between break by cascade case cast catch check ' +
    'checkpoint close clustered coalesce collate column commit constraint contains continue convert create ' +
    'cross current_timestamp current_user cursor database deallocate declare default delete deny desc ' +
    'disable distinct drop else enable end escape except exec execute exists exit external fetch filter ' +
    'first for foreign from full function go goto grant group having holdlock identity if in include index ' +
    'inner insert instead intersect into is join key left like matched merge next nocheck nocount ' +
    'nonclustered not null nullif of off offset on only open option or order outer output over partition ' +
    'percent period persisted pivot primary print proc procedure raiserror read readonly recompile ' +
    'references return returns revert revoke right rollback row rows rowguidcol schema schemabinding ' +
    'select sequence set some synonym system_time system_versioning table then throw to top tran ' +
    'transaction trigger truncate try type union unique unpivot update use using values view waitfor ' +
    'when where while with generated always start hidden masked cycle cache increment minvalue maxvalue ' +
    'no action replication encryption columnstore memory_optimized durability history_table'
  ).split(' '));

  const TYPES = new Set((
    'bigint int smallint tinyint bit decimal numeric money smallmoney float real date datetime datetime2 ' +
    'datetimeoffset smalldatetime time char varchar nchar nvarchar text ntext binary varbinary image ' +
    'uniqueidentifier xml sql_variant sysname rowversion timestamp geography geometry hierarchyid max'
  ).split(' '));

  const isWordStart = c => /[A-Za-z_#]/.test(c);
  const isWord = c => /[\w#$]/.test(c);

  function highlight(lines) {
    let depth = 0;        // nesting depth of an open /* comment */
    let close = null;     // closing char of an open quoted literal
    let quoteCls = '';

    function consumeQuoted(line, i) {
      while (i < line.length) {
        if (line[i] === close) {
          if (line[i + 1] === close) { i += 2; continue; }
          close = null;
          return i + 1;
        }
        i++;
      }
      return i;
    }

    function consumeComment(line, i) {
      while (i < line.length && depth > 0) {
        if (line.startsWith('/*', i)) { depth++; i += 2; }
        else if (line.startsWith('*/', i)) { depth--; i += 2; }
        else i++;
      }
      return i;
    }

    return lines.map(line => {
      if (line == null) return null;
      const runs = [];
      const n = line.length;
      let i = 0;
      while (i < n) {
        const start = i;
        if (depth > 0) { i = consumeComment(line, i); runs.push([start, i, 'hc']); continue; }
        if (close) { i = consumeQuoted(line, i); if (quoteCls) runs.push([start, i, quoteCls]); continue; }
        const c = line[i];
        if (line.startsWith('--', i)) { runs.push([i, n, 'hc']); break; }
        if (line.startsWith('/*', i)) { depth = 1; i = consumeComment(line, i + 2); runs.push([start, i, 'hc']); continue; }
        if (c === "'" || c === '"' || c === '[') {
          close = c === '[' ? ']' : c;
          quoteCls = c === "'" ? 'hs' : '';
          i = consumeQuoted(line, i + 1);
          if (quoteCls) runs.push([start, i, quoteCls]);
          continue;
        }
        if (c === '@') {
          i++;
          while (i < n && (isWord(line[i]) || line[i] === '@')) i++;
          runs.push([start, i, 'hv']);
          continue;
        }
        if (/\d/.test(c) && (i === 0 || !isWord(line[i - 1]))) {
          while (i < n && /[\d.eE]/.test(line[i])) i++;
          runs.push([start, i, 'hn']);
          continue;
        }
        if (isWordStart(c)) {
          while (i < n && isWord(line[i])) i++;
          const w = line.slice(start, i).toLowerCase();
          if (KEYWORDS.has(w)) runs.push([start, i, 'hk']);
          else if (TYPES.has(w)) runs.push([start, i, 'ht']);
          continue;
        }
        i++;
      }
      return runs;
    });
  }

  global.SqlHighlight = { highlight };
})(window);
