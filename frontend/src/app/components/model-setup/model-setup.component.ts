import { Component, EventEmitter, Input, OnChanges, Output } from '@angular/core'
import { CommonModule, DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms'
import { ModelCatalog, ModelService } from '../../services/model.service'
import { errorMessage } from '../../services/http-error'

/**
 * "Download a model to continue" — shown before sign-in when none of the
 * three supported models is installed. Emits `installed` once one is.
 */
@Component({
  selector: 'app-model-setup',
  imports: [CommonModule, FormsModule, DecimalPipe],
  templateUrl: './model-setup.component.html',
  styleUrl: './model-setup.component.css'
})
export class ModelSetupComponent implements OnChanges {
  @Input({ required: true }) catalog!: ModelCatalog
  @Output() installed = new EventEmitter<string>()
  @Output() retry = new EventEmitter<void>()

  selected: string | null = null
  installing = false
  error = ''

  constructor(public service: ModelService) {}

  ngOnChanges() {
    if (!this.selected && this.catalog) this.selected = this.catalog.recommended
  }

  tooBig(minRamGb: number): boolean {
    return this.catalog.ramGb > 0 && this.catalog.ramGb < minRamGb
  }

  async download() {
    if (!this.selected) return
    this.installing = true
    this.error = ''
    try {
      await this.service.installModel(this.selected, true)
      this.installed.emit(this.selected)
    } catch (e) {
      this.error = errorMessage(e, 'Download failed. Check your connection and that Ollama is running.')
    } finally {
      this.installing = false
    }
  }
}
