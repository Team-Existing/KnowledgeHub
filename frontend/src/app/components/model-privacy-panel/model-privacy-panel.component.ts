import { Component, EventEmitter, Output } from '@angular/core'
import { Router } from '@angular/router'
import { ModelService } from '../../services/model.service'
import { IconComponent } from '../../components/icon/icon.component'

@Component({
  selector: 'app-model-privacy-panel',
  imports: [IconComponent],
  templateUrl: './model-privacy-panel.component.html',
  styleUrl: './model-privacy-panel.component.css'
})
export class ModelPrivacyPanelComponent {
  @Output() close = new EventEmitter<void>()

  constructor(public service: ModelService, private router: Router) {}

  providerClass(): string {
    return this.service.modelStatus()?.llm.installed ? 'pill pill-green' : 'pill pill-red'
  }

  goTo(path: string) {
    this.close.emit()
    this.router.navigate([path])
  }
}
