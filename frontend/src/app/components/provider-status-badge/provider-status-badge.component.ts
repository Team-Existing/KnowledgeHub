import { Component, OnInit } from '@angular/core'
import { CommonModule } from '@angular/common'
import { ModelService } from '../../services/model.service'
import { ModelPrivacyPanelComponent } from '../model-privacy-panel/model-privacy-panel.component'

@Component({
  selector: 'app-provider-status-badge',
  imports: [CommonModule, ModelPrivacyPanelComponent],
  templateUrl: './provider-status-badge.component.html',
  styleUrl: './provider-status-badge.component.css'
})
export class ProviderStatusBadgeComponent implements OnInit {
  showPanel = false

  constructor(public service: ModelService) {}

  ngOnInit() { this.service.loadModelStatus() }

  togglePanel() { this.showPanel = !this.showPanel }
}
