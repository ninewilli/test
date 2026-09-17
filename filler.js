const answerEntries = arguments[0];
const options = arguments[1] || {};

const normalize = (value) => String(value || '')
  .replace(/[\s\u00a0]+/g, '')
  .replace(/^[（(]?\d+[）).、．:]*/, '')
  .replace(/[，。；：、,.!?！？“”"'（）()\[\]【】《》<>·—-]/g, '')
  .toLowerCase();

const textOf = (element) => (element && (element.innerText || element.textContent)) || '';

const questionSelectors = [
  '.questionLi', '.TiMu', '.question-item', '.subject-item', '.topic-item',
  '[data-question-id]', '[data-questionid]', 'li[id^="question"]'
];

const titleSelectors = [
  '.stem', '.mark_name', '.Zy_TItle', '.question-title', '.subject-title',
  '.topic-title', '.qtContent', '.question-content', '[data-role="question-title"]'
];

function collectQuestionContainers() {
  const explicit = [...document.querySelectorAll(questionSelectors.join(','))];
  const controls = [...document.querySelectorAll(
    'input[type="radio"],input[type="checkbox"],input[type="text"],textarea,[contenteditable="true"]'
  )];
  const inferred = controls.map((control) => {
    for (const selector of questionSelectors) {
      const found = control.closest(selector);
      if (found) return found;
    }
    return control.closest('li, section, article, fieldset, .form-group, .clearfix') || control.parentElement;
  });
  const unique = [...new Set([...explicit, ...inferred])].filter((element) => (
    element && element.getClientRects().length > 0
  ));
  // Some Chaoxing pages nest .TiMu inside .questionLi. Keep the innermost
  // question node so one question is not counted and filled twice.
  return unique.filter((candidate) => !unique.some((other) => (
    other !== candidate && candidate.contains(other)
  )));
}

function questionTitle(container) {
  for (const selector of titleSelectors) {
    const title = container.querySelector(selector);
    if (title && normalize(textOf(title))) return textOf(title).trim();
  }
  const clone = container.cloneNode(true);
  clone.querySelectorAll('input,textarea,button,select,option,script,style').forEach((node) => node.remove());
  return textOf(clone).trim();
}

function searchableEntryValues(entry) {
  const values = [];
  if (entry.question) values.push(entry.question);
  if (entry.answer_text != null) values.push(...answerValues(entry.answer_text));
  if (entry.note) values.push(entry.note);
  // Text answers sometimes appear verbatim in the stem. Choice letters alone
  // are intentionally not searchable because A/B/C are not unique identifiers.
  if (String(entry.type || '').toLowerCase() === 'text') {
    values.push(...answerValues(entry.answer));
  }
  return values.map((value) => String(value).trim()).filter(Boolean);
}

function entryMatches(entry, container, title, index) {
  if (entry.id != null) {
    const ids = [container.id, container.dataset.questionId, container.dataset.questionid].filter(Boolean);
    if (!ids.some((id) => String(id) === String(entry.id))) return false;
  }
  if (entry.id != null) return true;

  const actualTitle = normalize(title);
  if (entry.question) {
    const expected = normalize(entry.question);
    return actualTitle === expected || actualTitle.includes(expected) || expected.includes(actualTitle);
  }
  const optionText = [...container.querySelectorAll(
    'label,li,.option,.answerOption,[data-option]'
  )].map((node) => normalize(textOf(node))).filter(Boolean).join('|');
  const searchable = searchableEntryValues(entry).map(normalize).filter((value) => value.length >= 1 && !/^[a-z]$/i.test(value));
  if (searchable.length) {
    const type = String(entry.type || '').toLowerCase();
    const matches = searchable.filter((expected) => (
      actualTitle.includes(expected) || optionText.includes(expected)
    ));
    const found = type === 'multiple' && searchable.length > 1
      ? matches.length === searchable.length
      : matches.length > 0;
    if (found || options.searchOnly) return found;
  }

  // Legacy records containing only an option letter still need deterministic
  // compatibility with the old, order-based answer files.
  return !options.searchOnly && entry.index != null && Number(entry.index) === index + 1;
}

function describeChoice(control, position) {
  const isInput = control.matches && control.matches('input[type="radio"],input[type="checkbox"]');
  const input = isInput ? control : control.querySelector('input[type="radio"],input[type="checkbox"]');
  const label = input && input.id ? document.querySelector(`label[for="${CSS.escape(input.id)}"]`) : null;
  const row = (isInput ? input.closest('li,.option,.answerOption,.clearfix') : control)
    || label || (input && input.closest('label'))
    || (input && input.parentElement);
  let text = textOf(row).trim();
  text = text.replace(/^[\s\u00a0]*[A-ZＡ-Ｚ][.、．:：)）\s]+/i, '').trim();
  return { input, row, text, letter: String.fromCharCode(65 + position) };
}

function rowIsSelected(choice) {
  if (choice.input) return !!choice.input.checked;
  if (choice.row.getAttribute('aria-checked') === 'true') return true;
  const classTokens = String(choice.row.className || '').split(/\s+/);
  return classTokens.some((token) => /^(cur|current|selected|checked|on|active)$/i.test(token));
}

function setChoiceSelected(choice, checked) {
  if (choice.input) {
    setChecked(choice.input, checked);
    return;
  }
  if (rowIsSelected(choice) !== checked) choice.row.click();
}

function setChecked(input, checked) {
  if (input.checked === checked) return;
  input.click();
  if (input.checked === checked) return;
  const prototype = Object.getPrototypeOf(input);
  const descriptor = Object.getOwnPropertyDescriptor(prototype, 'checked');
  if (descriptor && descriptor.set) descriptor.set.call(input, checked);
  else input.checked = checked;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
}

function setValue(element, value) {
  if (element.isContentEditable) {
    element.textContent = value;
  } else {
    const prototype = Object.getPrototypeOf(element);
    const descriptor = Object.getOwnPropertyDescriptor(prototype, 'value');
    if (descriptor && descriptor.set) descriptor.set.call(element, value);
    else element.value = value;
  }
  element.dispatchEvent(new Event('input', { bubbles: true }));
  element.dispatchEvent(new Event('change', { bubbles: true }));
}

function setTextControl(container, element, index, value) {
  const text = String(value);
  let editorUpdated = false;

  if (element.tagName === 'TEXTAREA' && element.id && window.UE && window.UE.getEditor) {
    try {
      const editor = window.UE.getEditor(element.id);
      editor.setContent(text);
      if (editor.sync) editor.sync();
      editorUpdated = true;
    } catch (_) {
      // Use the DOM-based UEditor fallback below.
    }
  }

  const editorFrames = [...container.querySelectorAll('iframe[id^="ueditor_"]')]
    .filter((frame) => frame.getClientRects().length > 0);
  const frame = editorFrames[index];
  if (frame) {
    try {
      const body = frame.contentDocument && frame.contentDocument.body;
      if (body) {
        body.textContent = text;
        body.dispatchEvent(new Event('input', { bubbles: true }));
        body.dispatchEvent(new Event('change', { bubbles: true }));
        editorUpdated = true;
      }
    } catch (_) {
      // The backing textarea is still updated below.
    }
  }

  setValue(element, text);
  return editorUpdated;
}

function answerValues(answer) {
  return (Array.isArray(answer) ? answer : [answer]).map((value) => String(value).trim());
}

function choiceMatches(choice, requested) {
  const wanted = normalize(requested);
  return wanted === choice.letter.toLowerCase()
    || wanted === normalize(choice.text)
    || normalize(choice.text).includes(wanted);
}

function selectChoices(choices, requested) {
  return requested.map((value) => choices.find((choice) => choiceMatches(choice, value)));
}

function fillQuestion(container, entry) {
  const type = String(entry.type || '').toLowerCase();
  const inputs = [...container.querySelectorAll('input[type="radio"],input[type="checkbox"]')];
  const optionRows = inputs.length ? [] : [...container.querySelectorAll(
    '.Zy_ulTop > li,.answerList > li,.option-list > li,.options > li,[data-option]'
  )].filter((row) => row.getClientRects().length > 0);
  const textInputs = [...container.querySelectorAll('input[type="text"],textarea,[contenteditable="true"]')];

  if (type === 'text' || (!inputs.length && textInputs.length)) {
    const values = answerValues(entry.answer);
    if (!textInputs.length) return { status: 'error', detail: '未找到文本输入框' };
    if (values.length !== 1 && values.length !== textInputs.length) {
      return { status: 'error', detail: `答案有 ${values.length} 项，但页面有 ${textInputs.length} 个输入框` };
    }
    let richEditorCount = 0;
    textInputs.forEach((input, index) => {
      const value = values.length === 1 ? values[0] : values[index];
      if (setTextControl(container, input, index, value)) richEditorCount += 1;
    });
    const editorDetail = richEditorCount ? `，同步 ${richEditorCount} 个 UEditor` : '';
    return { status: 'filled', detail: `已填写 ${textInputs.length} 个文本框${editorDetail}` };
  }

  const controls = inputs.length ? inputs : optionRows;
  if (!controls.length) return { status: 'error', detail: '未找到选项控件' };
  const choices = controls.map(describeChoice);
  const fallbackRequested = answerValues(entry.answer);
  const titleText = questionTitle(container);
  const hasCheckbox = inputs.some((input) => input.type === 'checkbox');
  const hasRadio = inputs.some((input) => input.type === 'radio');
  const pageIsMultiple = hasCheckbox
    || (!hasRadio && (/多选题/.test(titleText) || type === 'multiple' || Array.isArray(entry.answer)));

  if (!pageIsMultiple && fallbackRequested.length > 1) {
    return {
      status: 'error',
      detail: `页面控件是单选题，但答案包含 ${fallbackRequested.length} 个选项`
    };
  }
  let requested = entry.answer_text != null
    ? answerValues(entry.answer_text)
    : (type === 'single' && entry.note ? answerValues(entry.note) : fallbackRequested);
  let selected = selectChoices(choices, requested);
  let matchMode = requested === fallbackRequested ? '字母/答案值' : '答案文字';

  // Text is safer when options are shuffled. If the page wording changed,
  // fall back to the supplied letters rather than failing the whole question.
  if (selected.some((choice) => !choice) && requested !== fallbackRequested) {
    requested = fallbackRequested;
    selected = selectChoices(choices, requested);
    matchMode = '字母后备';
  }
  if (selected.some((choice) => !choice)) {
    const missing = requested.filter((_, index) => !selected[index]);
    return { status: 'error', detail: `未找到选项: ${missing.join(', ')}` };
  }
  if (new Set(selected).size !== selected.length) {
    return { status: 'error', detail: '多个答案匹配到了同一个选项' };
  }

  const wantedRows = new Set(selected.map((choice) => choice.row));
  if (pageIsMultiple) {
    choices.forEach((choice) => setChoiceSelected(choice, wantedRows.has(choice.row)));
  } else {
    setChoiceSelected(selected[0], true);
  }
  return {
    status: 'filled',
    detail: `已选择 ${selected.map((choice) => choice.letter).join(', ')}（${pageIsMultiple ? '多选' : '单选'}，${matchMode}）`
  };
}

const containers = collectQuestionContainers();
const usedEntries = new Set();
const results = [];

containers.forEach((container, index) => {
  const title = questionTitle(container);
  const candidates = answerEntries
    .map((entry, entryIndex) => ({ entry, entryIndex }))
    .filter(({ entry, entryIndex }) => !usedEntries.has(entryIndex) && entryMatches(entry, container, title, index));

  if (candidates.length === 0) {
    results.push({ status: 'unmatched-question', question: title, questionIndex: index + 1 });
    return;
  }
  if (candidates.length > 1) {
    results.push({ status: 'ambiguous', question: title, questionIndex: index + 1 });
    return;
  }

  const { entry, entryIndex } = candidates[0];
  usedEntries.add(entryIndex);
  const outcome = fillQuestion(container, entry);
  container.dataset.ucasAnswerFiller = outcome.status;
  container.style.outline = outcome.status === 'filled' ? '2px solid #1f9d55' : '2px solid #d64545';
  results.push({
    ...outcome,
    question: title,
    questionIndex: index + 1,
    entryIndex,
    answerSet: entry._answer_set || null
  });
});

return {
  pageTitle: document.title,
  questionCount: containers.length,
  results,
  usedEntryIndexes: [...usedEntries]
};
