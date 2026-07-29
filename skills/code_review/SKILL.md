---
name: code_review
description: Use this skill when the user asks to review code, find bugs, or improve code quality. Provides a structured code review methodology.
---

# code_review

## Overview
This skill teaches the agent how to perform a thorough, structured code review.

## Instructions
When reviewing code, follow these steps:

1. **Read the code completely** before commenting.
2. **Check correctness first**: logic errors, off-by-one, null handling, edge cases.
3. **Check readability**: naming, function length, comments where needed.
4. **Check security**: injection, unsafe input handling, secrets in code.
5. **Group feedback by severity**: 🔴 critical / 🟡 suggestion / 🔵 nitpick.
6. **Always give a concrete fix suggestion**, not just "this is wrong".

## Output Format
```
## 代码评审结果
### 🔴 Critical (必须修改)
- ...
### 🟡 Suggestions (建议修改)
- ...
### 🔵 Nitpicks (可选)
- ...
### 总结
一句话总体评价。
```
