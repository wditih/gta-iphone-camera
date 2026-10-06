// SPDX-License-Identifier: GPL-3.0-only
#pragma once
namespace phonecamera {
void install();
// Called exclusively on the editor camera thread; true suppresses extra shake.
bool apply(void* director);
}
